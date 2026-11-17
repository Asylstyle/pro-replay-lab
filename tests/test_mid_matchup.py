import unittest
from unittest.mock import patch

from pro_replay.mid_matchup import catalog, mid_matchup_review, render_mid_matchup


def sample(a=13, b=25):
    players = []
    for side in (0, 128):
        for idx in range(5):
            players.append({"player_slot": side + idx, "hero_id": (a if side == 0 else b) if idx == 0 else 1 + idx,
                            "lane_role": 2 if idx == 0 else 1, "is_roaming": False})
    return {"start_time": catalog()["patch_start"] + 600, "players": players}


class MidMatchupTests(unittest.TestCase):
    def test_ability_draft_and_turbo_do_not_show_normal_hero_progression(self):
        for mode in (18, 23):
            self.assertEqual(mid_matchup_review(dict(sample(), game_mode=mode), 0, 2)["status"], "unavailable")

    def test_patch_roster_and_level_attack(self):
        review = mid_matchup_review(sample(), 0, 2)
        self.assertEqual(review["status"], "available")
        self.assertEqual(review["self"]["levels"][0]["attack"], [45.0, 53.0])
        self.assertEqual(review["self"]["levels"][5]["attack"], [64.0, 72.0])
        self.assertEqual(review["opponent"]["levels"][0]["attack"], [49.0, 57.0])
        self.assertEqual(len(catalog()["heroes"]), 127)
        for hero in catalog()["heroes"].values():
            self.assertGreater(hero["attack_range"], 0)
            self.assertGreater(hero["attack_rate"], 0)

    def test_universal_attack_and_void_innate_are_counted_once(self):
        review = mid_matchup_review(sample(21, 126), 0, 2)
        self.assertEqual(review["self"]["levels"][0]["attack"], [49.2, 61.2])
        self.assertEqual(review["opponent"]["levels"][0]["attack"], [52.7, 56.7])

    def test_potential_or_learned_at_level(self):
        raw = sample()
        raw["players"][0]["ability_upgrades_arr"] = [5071, 5069, 5069, 5072, 5069, 5073]
        review = mid_matchup_review(raw, 0, 2)
        first = review["self"]
        self.assertEqual(first["spell"]["name"], "Illusory Orb")
        self.assertEqual([r["rank"] for r in first["levels"]], [1, 1, 2, 2, 3, 3, 4])
        self.assertFalse(first["levels"][0]["observed"])
        self.assertIn("до ранга", render_mid_matchup(review))
        raw["players"][0]["ability_upgrades"] = [
            {"ability": aid, "level": level} for aid,level in zip(raw["players"][0]["ability_upgrades_arr"], [1, 3, 4, 4, 5, 6])]
        first = mid_matchup_review(raw, 0, 2)["self"]
        self.assertEqual([r["rank"] for r in first["levels"]], [0, 0, 1, 2, 3, 3, 4])
        self.assertTrue(first["levels"][0]["observed"])
        self.assertFalse(first["levels"][-1]["observed"])
        self.assertEqual(first["ult_ranks"], {6: 1})

    def test_later_power_spikes_require_the_right_hero_and_level(self):
        raw = sample(13, 82)  # Puck and Meepo have different ultimate gates.
        for p in (raw["players"][0], raw["players"][5]):
            p.update(times=[0, 600, 1200], xp_t=[0, 8199, 17500])
        raw["players"][0]["ability_upgrades"] = [
            {"ability": 5073, "level": n} for n in (6, 13, 18)]
        review = mid_matchup_review(raw, 0)
        self.assertEqual(review["self"]["ult_ranks"], {6: 1, 12: 1, 18: 3})
        self.assertEqual(review["self"]["level_windows"][12], (600, 1200))
        self.assertEqual(review["self"]["level_windows"][18], (600, 1200))
        self.assertIsNone(review["opponent"]["ultimate"])
        html = render_mid_matchup(review)
        self.assertIn("На 7-м доступен ранг 4", html)
        self.assertIn("12 ур. — ранг 1 в журнале", html)
        standard, marker, spellbooks = html.partition('<details class="hero-spellbook">')
        self.assertTrue(marker)
        self.assertIn("Dream Coil", standard)
        self.assertNotIn("Divided We Stand", standard)
        self.assertIn("Divided We Stand", spellbooks)
        self.assertIn("для первого клона указан уровень 3", spellbooks)

    def test_opening_priority_and_curated_tiebreak(self):
        raw = sample()
        player = raw["players"][0]
        player["ability_upgrades_arr"] = [5071, 5069]
        self.assertEqual(mid_matchup_review(raw, 0)["self"]["spell"]["name"], "Illusory Orb")
        player.pop("ability_upgrades_arr")
        player["ability_upgrades"] = [{"ability": a, "level": level}
                                      for a, level in ((5071, 1), (5069, 2), (5071, 3))]
        self.assertEqual(mid_matchup_review(raw, 0)["self"]["spell"]["name"], "Waning Rift")

    def test_xp_intervals_respect_incomplete_or_overlapping_samples(self):
        raw = sample()
        for p, xp in ((raw["players"][0], [0, 640, 1500]), (raw["players"][5], [0, 239, 640])):
            p.update(times=[0, 60, 120], xp_t=xp)
        result = mid_matchup_review(raw, 0)
        self.assertEqual(result["self"]["level_windows"][3], (0, 60))
        self.assertEqual(result["opponent"]["level_windows"][3], (60, 120))
        self.assertIn("<td>Puck</td>", render_mid_matchup(result))
        raw["players"][5]["xp_t"][1] = 640
        self.assertIn("Порядок не виден", render_mid_matchup(mid_matchup_review(raw, 0)))
        raw["players"][5]["xp_t"] = [0, 1500, 600]
        self.assertFalse(mid_matchup_review(raw, 0)["opponent"]["level_windows"])

    def test_ambiguous_lane_or_other_patch(self):
        raw = sample()
        self.assertIsNone(mid_matchup_review(raw, 1, 1))
        self.assertEqual(mid_matchup_review(raw, 0, patch="7.41e")["status"], "unavailable")
        raw["players"][6]["lane_role"] = 2
        self.assertEqual(mid_matchup_review(raw, 0)["status"], "unavailable")
        raw["players"][6]["is_roaming"] = True
        self.assertEqual(mid_matchup_review(raw, 0)["status"], "available")
        raw["players"][5]["lane_role"] = 3
        raw["players"][5]["position_est"] = 2
        self.assertEqual(mid_matchup_review(raw, 0)["status"], "unavailable")
        raw["start_time"] = catalog()["patch_start"] - 1
        self.assertEqual(mid_matchup_review(raw, 0, 2)["status"], "unavailable")
        self.assertEqual(render_mid_matchup(None), "")

    def test_cached_match_patch_cannot_extend_valve_snapshot(self):
        raw = sample()
        checked = catalog()["checked_through"]
        raw["start_time"] = checked + 100
        calendar = [(catalog()["patch_start"], "7.41f"), (checked + 50, "7.42")]
        with (patch("pro_replay.mid_matchup.time.time", return_value=checked + 200),
              patch("pro_replay.mid_matchup._verified_calendar", return_value=(calendar, checked + 190)) as verify):
            self.assertEqual(mid_matchup_review(raw, 0, patch="7.41f")["status"], "unavailable")
            verify.assert_called_once()
