import copy
from pathlib import Path
import tempfile
import unittest

from pro_replay.coach_model import assess_player, evaluate_ranked, load_coach_model, save_coach_model, train_coach_model
from pro_replay.impact import SLOTS


def replay(index):
    match = {"match_id": 100 + index, "start_time": 1700000000 + 7200 * index, "duration": 1800,
             "leagueid": 77, "league": {"tier": "professional"}, "lobby_type": 1, "players": []}
    for i, slot in enumerate(SLOTS):
        value = 20 + 4 * (i % 5) + index
        match["players"].append({"player_slot": slot, "hero_id": 1 + i, "position_est": 5 - i % 5,
                                 "times": [0, 600, 1200, 1260], "lh_t": [0, value, value * 3 + 2, 5000],
                                 "gold_t": [0, 100 * value, 220 * value, 90000],
                                 "xp_t": [0, 80 * value, 200 * value, 90000]})
    return match


class CoachingModelTests(unittest.TestCase):
    def setUp(self):
        self.games = [replay(i) for i in range(10)]
        self.model = train_coach_model(self.games)

    def test_fits_only_verified_tournaments_with_full_teams_and_unique_ids(self):
        unknown_tier = dict(replay(20), league={"tier": "amateur"})
        ranked = dict(replay(21), leagueid=0, lobby_type=7)
        partial = replay(22)
        partial["players"] = partial["players"][:9]
        model = train_coach_model([*reversed(self.games), self.games[0], unknown_tier, ranked, partial])
        self.assertEqual(model["fit"], self.model["fit"])
        self.assertEqual(model["coverage"]["excluded"], {"outside_verified_pro_leagues": 2,
                         "incomplete_roster_or_snapshots": 1, "duplicate_match": 1})
        self.assertEqual(model["coverage"]["train_match_ids"], list(range(100, 108)))
        self.assertEqual(model["coverage"]["test_match_ids"], [108, 109])
        for metric in model["metrics"].values():
            self.assertEqual(metric["n"], 20)

    def test_unfinished_games_at_time_boundary_excluded(self):
        self.games[7]["duration"] = 8000
        model = train_coach_model(self.games)
        self.assertEqual(model["coverage"]["train_games"], 7)
        self.assertEqual(model["coverage"]["excluded"], {"overlapping_train_test_boundary": 1})
        self.assertLessEqual(model["coverage"]["train_last_end"], model["coverage"]["test_first_start"])

    def test_future_values_and_full_match_estimated_position_never_enter_prediction(self):
        original = assess_player(self.model, self.games[-1], 0)
        self.assertEqual(original["farm_rank_10"], 5)
        self.assertEqual(original["cohort"], "holdout")
        changed = copy.deepcopy(self.games[-1])
        for p in changed["players"]:
            p["position_est"] = 1
            p["kills"] = 30
            for series in ("lh_t", "gold_t", "xp_t"):
                p[series][2:] = [500000, 500010]
        altered = assess_player(self.model, changed, 0)
        self.assertEqual([m["expected"] for m in original["metrics"]], [m["expected"] for m in altered["metrics"]])
        self.games[-1] = changed
        retrained = train_coach_model(self.games)
        self.assertEqual(retrained["fit"], self.model["fit"])
        self.assertNotEqual(retrained["metrics"], self.model["metrics"])

    def test_missing_exact_snapshot_or_gold_rank_is_explicit(self):
        no_timestamps = copy.deepcopy(self.games[0])
        player = no_timestamps["players"][0]
        player.pop("times")
        for series in ("lh_t", "gold_t", "xp_t"):
            player[series] = list(range(21))
        self.assertEqual(assess_player(self.model, no_timestamps, 0)["status"], "unavailable")
        changed = copy.deepcopy(self.games[0])
        changed["players"][0]["times"][2] = 1201
        assessment = assess_player(self.model, changed, 0)
        self.assertEqual(assessment["status"], "ok")
        self.assertTrue(all(m["actual"] is None and m["gap"] is None for m in assessment["metrics"]))
        changed["players"][4]["times"][1] = 601
        self.assertEqual(assess_player(self.model, changed, 0)["status"], "unavailable")

    def test_bundle_reload_and_unseen_hero_have_finite_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "bundle.json")
            save_coach_model(self.model, path)
            restored = load_coach_model(path)
            self.assertEqual(restored, self.model)
        changed = copy.deepcopy(self.games[0])
        changed["match_id"] = 999
        changed["players"][0]["hero_id"] = 500
        assessment = assess_player(self.model, changed, 0)
        self.assertEqual((assessment["cohort"], assessment["hero_train_n"]), ("external", 0))
        for target in assessment["metrics"]:
            self.assertGreaterEqual(target["expected"], assessment["at10"][target["key"]])
            self.assertAlmostEqual(target["gap"], target["actual"] - target["expected"], places=2)

    def test_ranked_transfer_uses_frozen_model_and_filters_complete_independent_games(self):
        frozen = copy.deepcopy(self.model)
        newer = dict(replay(20), leagueid=0, lobby_type=7, game_mode=22)
        cutoff = self.model["coverage"]["train_last_end"]
        earlier = dict(newer, match_id=998, start_time=cutoff - 1000)
        overlapping_id = dict(newer, match_id=self.model["coverage"]["test_match_ids"][0])
        short = dict(newer, match_id=996, duration=1100)
        out = evaluate_ranked(self.model, [earlier, newer, overlapping_id, newer, short, self.games[0]])
        self.assertEqual(self.model, frozen)
        self.assertEqual(out["all_ranked"]["match_ids"], [998, newer["match_id"]])
        self.assertEqual(out["after_train"]["match_ids"], [newer["match_id"]])
        self.assertEqual(out["excluded"], {"overlap_with_pro_train_or_test": 1, "duplicate_ranked_match": 1,
                                           "ended_before_minute_20": 1, "outside_ranked_all_pick": 1})
        expected = [assess_player(self.model, newer, slot)["metrics"][0] for slot in SLOTS]
        metric = out["after_train"]["metrics"]["lh"]
        self.assertEqual(metric["n"], 10)
        self.assertAlmostEqual(metric["mae"], sum(abs(m["gap"]) for m in expected) / 10, places=2)
        self.assertEqual(metric["baseline_mae"], sum(abs(m["baseline"] - m["actual"]) for m in expected) / 10)
        self.assertIsNone(evaluate_ranked(self.model, [earlier])["after_train"]["metrics"]["xp"]["mae"])
