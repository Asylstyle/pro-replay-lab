import copy
import unittest

from pro_replay.coach_features import game_features, row_features
from tests.test_coach_model import replay


class EconomyFeatureTests(unittest.TestCase):
    def test_future_and_final_role_do_not_change_predictors(self):
        raw = replay(3)
        for p in raw["players"]:
            p["times"].insert(1, 300)
            for field in ("lh_t", "gold_t", "xp_t"):
                p[field].insert(1, p[field][1] // 2)
            p["dn_t"] = [0, 2, 5, 9, 10]
        before = row_features(raw)
        changed = copy.deepcopy(raw)
        changed["radiant_win"] = True
        for p in changed["players"]:
            p.update(position_est=1, kills=100, gold_per_min=1000)
            for field in ("lh_t", "gold_t", "xp_t", "dn_t"):
                p[field][3:] = [99999, 999999]
        after = row_features(changed)
        self.assertEqual([r["features"] for r in before.values()], [r["features"] for r in after.values()])
        self.assertEqual(before[0]["features"]["gain5to10_lh"], 12)
        self.assertEqual(before[0]["features"]["own_denies_10"], 5)
        self.assertAlmostEqual(sum(before[s]["features"]["gold_share_10"] for s in range(5)), 1)

    def test_missing_timestamps_stay_missing_and_no_forward_fill(self):
        raw = replay(1)
        rows = row_features(raw)
        self.assertIsNone(rows[0]["features"]["at5_lh"])
        self.assertIsNone(rows[0]["features"]["own_denies_10"])
        raw["players"][0]["times"].insert(1, 301)
        raw["players"][0]["lh_t"].insert(1, 12)
        self.assertIsNone(row_features(raw)[0]["features"]["at5_lh"])

    def test_training_adapter_reuses_complete_game_checks(self):
        game, reason = game_features(replay(2))
        self.assertIsNone(reason)
        self.assertEqual(len(game["rows"]), 10)
        self.assertTrue(all("features" in r for r in game["rows"]))
        raw = replay(2)
        raw["players"].pop()
        self.assertIsNone(game_features(raw)[0])
