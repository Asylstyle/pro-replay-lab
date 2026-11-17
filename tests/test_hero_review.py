import json
import unittest

from pro_replay.hero_review import extract_hero_samples, hero_benchmark, render_hero_review
from pro_replay.impact import SLOTS


def raw_game():
    players = []
    for i, slot in enumerate(SLOTS):
        players.append({"player_slot": slot, "hero_id": 10 + i, "position_est": i % 5 + 1,
                        "kills": 1, "assists": 0, "tower_damage": 100 if i == 5 else 0,
                        "hero_damage": 1000, "hero_healing": 0, "obs_placed": 0,
                        "observer_kills": 0, "stuns": 1})
    return {"players": players, "duration": 600}


def row(match_id, **kwargs):
    return {"match_id": match_id, "hero_id": 15, "hero_name": "Ursa", "league_id": 10,
            "game_mode": 2, "lobby_type": 1, "patch": 60, "patch_version": "7.41f",
            "duration": 600, "start_time": 1000 * match_id, "detail": {"slot": 128},
            "hero_samples": extract_hero_samples(raw_game()), **kwargs}


class HeroReviewTests(unittest.TestCase):
    def test_both_sides_without_account_ids_and_zero_team_total(self):
        target = row(2)
        self.assertEqual(len(json.loads(json.dumps(target["hero_samples"]))), 10)
        result = hero_benchmark(target, [row(1), row(1), target])
        self.assertEqual((result["same_n"], result["other_n"]), (1, 1))
        tower = result["metrics"]["buildings"]
        self.assertEqual((tower["rate"], tower["share"], tower["hero_rate"]["median"]), (10, 1, 10))
        self.assertEqual(result["metrics"]["healing"]["hero_rate"]["median"], 0)
        self.assertIsNone(result["metrics"]["healing"]["hero_share"]["median"])
        self.assertEqual(tower["position_rate"]["median"], 0)

    def test_exclude_wrong_patch_population_position_time_and_incomplete_map(self):
        target = row(10)
        wrong_position = row(3)
        for s in wrong_position["hero_samples"]:
            s["position"] = 4
        earlier = row(1)
        earlier["hero_samples"][5]["team_totals"]["buildings"] = None
        result = hero_benchmark(target, [
            earlier, wrong_position, row(4, patch_version="7.41e"),
            row(5, league_id=0, lobby_type=7), row(6, game_mode=22),
            row(7, start_time=9700), row(10), row(11),
        ])
        self.assertEqual(result["examples"], [1])
        self.assertEqual(result["metrics"]["buildings"]["hero_rate"]["n"], 1)
        self.assertEqual(result["metrics"]["buildings"]["hero_share"]["n"], 0)
        self.assertEqual(result["metrics"]["buildings"]["hero_rate"]["middle_half"], None)
        target.pop("patch_version")
        self.assertEqual(hero_benchmark(target, [row(4, patch_version="7.41e")])["same_n"], 1)

    def test_missing_estimated_position_does_not_guess_from_lane_or_slot(self):
        raw = raw_game()
        del raw["players"][5]["position_est"]
        raw["players"][5].update(lane_role=1, is_roaming=False)
        target = row(10, hero_samples=extract_hero_samples(raw))
        result = hero_benchmark(target, [row(1)])
        self.assertIsNone(result["position"])
        self.assertEqual(result["same_n"], 0)
        self.assertIn("оценка позиции OpenDota отсутствует", render_hero_review(target, []))
        self.assertIn("Обнови", render_hero_review({"match_id": 12}, []))

    def test_incomplete_team_missing_player_and_missing_field_do_not_imply_zero(self):
        raw = raw_game()
        del raw["players"][7]["observer_kills"]
        raw["players"][0]["player_slot"] = raw["players"][1]["player_slot"]
        samples = extract_hero_samples(raw)
        self.assertEqual(len(samples), 8)
        for s in samples:
            if s["slot"] < 128:
                self.assertIsNone(s["team_totals"]["damage"])
            else:
                self.assertIsNone(s["team_totals"]["vision"])
                self.assertEqual(s["team_totals"]["damage"], 5000)

    def test_report_contains_historical_evidence_and_spread(self):
        target = row(8, hero_name="<img>")
        result = hero_benchmark(target, [row(k) for k in range(1, 6)])
        self.assertEqual(result["metrics"]["buildings"]["hero_rate"]["middle_half"], [10.0, 10.0])
        html = render_hero_review(target, [row(k) for k in range(1, 6)])
        self.assertIn("https://www.opendota.com/matches/5", html)
        self.assertIn("25–75-й", html)
        self.assertIn("&lt;img&gt;", html)
        self.assertNotIn("<img>", html)

    def test_sparse_hero_history_uses_labeled_role_peers_for_combat(self):
        target = row(8)
        current = target["hero_samples"][5]
        current["values"]["control"] = 300
        current["team_totals"]["control"] = 304
        history = [row(k) for k in range(1, 7)]
        for earlier in history[1:]:
            earlier["hero_samples"][5]["hero_id"] = 100
        html = render_hero_review(target, history)
        self.assertIn("Тот же герой — одно наблюдение", html)
        self.assertIn("медиана других героев той же позиции", html)
        self.assertIn("В 11 из 11 предыдущих", html)
        self.assertIn("Игрок нанёс 100 урона строениям при командном итоге 100", html)
