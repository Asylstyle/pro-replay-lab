from copy import deepcopy
from datetime import datetime, timezone
from statistics import mean
import unittest

from pro_replay.coach_features import game_features
from scripts import score_ranked_collection as batch


class RankedCollectionScoreTests(unittest.TestCase):
    def test_frozen_pub_model_median_exclusions_and_exact_time_boundaries(self):
        folder = batch.EXAMPLES / "fresh-ranked-2026-11-17"
        training, receipt = batch.read_capture(folder / "matches.json.gz", folder / "manifest.json")
        model = batch.load_reference(batch.EXAMPLES / batch.PACKAGED_MODEL)
        fit = model["cohorts"]["ranked_7300"]["fit"]
        last = fit["train_last_end"]
        source = training[0]

        def card(mid, start):
            raw = deepcopy(source)
            raw.update(match_id=mid, start_time=start)
            raw["opendota_live"].update(match_id=mid, last_update_time=start + 200,
                observed_at_utc=datetime.fromtimestamp(start + 210, timezone.utc).isoformat())
            return raw

        past = card(1, last)
        later = card(2, last + 1)
        boundary = card(3, batch.RELEASE_TIME)
        forward = card(4, batch.RELEASE_TIME + 1)
        old_study = card(5, batch.RELEASE_TIME + 2)
        low_mmr = card(6, batch.RELEASE_TIME + 3)
        low_mmr["opendota_live"]["average_mmr"] = 7299
        receipt = dict(receipt, policy=dict(receipt["policy"], as_of=batch.RELEASE_TIME + 20000))
        report = batch.score([source, past, later, boundary, forward, old_study, low_mmr], receipt, model, {5})
        windows = report["evaluation"]
        self.assertEqual(report["excluded"], {"in_current_reference": 1, "in_previous_study": 1,
                                             "below_7300_average_mmr": 1})
        for group, ids in (("through_training_end", [1]), ("after_training_through_release", [2, 3]),
                           ("after_release", [4]), ("unseen", [1, 2, 3, 4])):
            self.assertEqual(windows[group]["match_ids"], ids)
            self.assertEqual(windows[group]["players"], len(ids) * 10)
        game, _ = game_features(forward, ranked=True)
        gold_column = fit["targets"].index("gold")
        expected_mae = mean(abs(row["at20"]["gold"] - row["at10"]["gold"] -
                                fit["baseline"][str(row["rank"])][gold_column]) for row in game["rows"])
        metrics = windows["after_release"]["metrics"]
        self.assertAlmostEqual(metrics["rank_median"]["gold"]["mae"], expected_mae)
        self.assertAlmostEqual(windows["after_release"]["model_minus_median"]["targets"]["gold"]["mae_delta"],
                               metrics["ranked_model"]["gold"]["mae"] - expected_mae)
        empty = batch.score([source], receipt, model, set())["evaluation"]["unseen"]
        self.assertEqual(empty["matches"], 0)
        self.assertIsNone(empty["metrics"]["ranked_model"]["gold"]["mae"])
        with self.assertRaisesRegex(ValueError, "published weights"):
            batch.score([], receipt, dict(model, counts={}), set())
