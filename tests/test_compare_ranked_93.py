from copy import deepcopy
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from statistics import mean
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pro_replay.coach_features import game_features
from pro_replay.ml_experiments import predict_bundle
from scripts import compare_ranked_93 as study


class Ranked93ComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        batch = study.EXAMPLES / "ranked-collection-2026-11-18"
        cls.training, receipt = study.read_capture(batch / "matches.json.gz", batch / "manifest.json")
        cls.old = study.load_reference(study.EXAMPLES / "patch-cohort-model.json")
        cls.new = study.load_reference(study.STUDY / "model.json")
        cls.protocol = study.read_json(study.STUDY / "protocol.json")
        cls.last = cls.protocol["latest_training_end"]
        cls.frozen = cls.protocol["frozen_at"]
        cls.deadline = cls.protocol["collection"]["deadline_utc_epoch"]
        cls.receipt = dict(receipt, policy=dict(receipt["policy"], as_of=cls.deadline))

    def card(self, mid, start, template=0):
        c = deepcopy(self.training[template])
        c.update(match_id=mid, start_time=start, duration=1201)
        utc = datetime.fromtimestamp(start + 90, timezone.utc).isoformat()
        c["opendota_live"].update(match_id=mid, last_update_time=start + 80, observed_at_utc=utc)
        return c

    def evaluate(self, cards, *, prior=(), protocol=None, receipt=None):
        return study.compare(cards, receipt or self.receipt, self.old, self.new,
                             protocol or self.protocol, set(prior))

    def test_identical_unseen_match_sets_time_boundaries_and_paired_metric(self):
        overlap = self.card(1, self.last)
        first_later = self.card(2, self.last + 1)
        at_freeze = self.card(3, self.frozen, template=1)
        forward = self.card(4, self.frozen + 1, template=2)
        historical = self.card(5, self.frozen + 3)
        low = self.card(6, self.frozen + 5)
        low["opendota_live"]["average_mmr"] = 7299
        incomplete = self.card(7, self.frozen + 7)
        incomplete["players"][0]["xp_t"] = None
        report = self.evaluate([forward, low, historical, self.training[-1], overlap, at_freeze,
                                incomplete, first_later], prior=[5])
        groups = report["evaluation"]
        self.assertEqual(groups["primary"]["match_ids"], [2, 3, 4])
        self.assertEqual(groups["primary"]["players"], 30)
        details = groups["primary"]["per_match"]
        self.assertEqual([r["match_id"] for r in details], [2, 3, 4])
        self.assertEqual(details[1]["avg_mmr"], at_freeze["opendota_live"]["average_mmr"])
        self.assertEqual(groups["overlap_diagnostic"]["match_ids"], [1])
        self.assertEqual(groups["after_freeze"]["match_ids"], [4])
        self.assertEqual(report["excluded"]["in_model_training"], 1)
        self.assertEqual(report["excluded"]["in_previous_study"], 1)
        self.assertEqual(report["excluded"]["below_7300_average_mmr"], 1)
        self.assertEqual(sum(report["excluded"].values()), 4)

        rows = [row for c in (first_later, at_freeze, forward) for row in game_features(c, ranked=True)[0]["rows"]]
        for key in ("gold", "xp"):
            errors = []
            for model, name in ((self.old, "old_53"), (self.new, "new_93")):
                fit = model["cohorts"]["ranked_7300"]["fit"]
                error = mean(abs(predict_bundle(fit, row)[key] - row["at20"][key]) for row in rows)
                errors.append(error)
                self.assertAlmostEqual(groups["primary"]["metrics"][name][key]["mae"], error)
                self.assertAlmostEqual(mean(r[name][key] for r in details), error)
            delta = groups["primary"]["new_minus_old"]["targets"][key]
            self.assertAlmostEqual(delta["mae_delta"], errors[1] - errors[0])
            self.assertAlmostEqual(delta["percent_change_vs_old"], (errors[1] / errors[0] - 1) * 100)
            self.assertEqual(len(delta["ci95"]), 2)
            self.assertIsNone(groups["after_freeze"]["new_minus_old"]["targets"][key]["ci95"])
            empty = self.evaluate([overlap])["evaluation"]["primary"]
            self.assertIsNone(empty["metrics"]["new_93"][key]["mae"])
            self.assertIsNone(empty["new_minus_old"]["targets"][key]["percent_change_vs_old"])

    def test_registered_models_training_end_deadline_and_unique_test_ids(self):
        self.assertEqual(max(c["start_time"] + c["duration"] for c in self.training), self.last)
        self.assertEqual({c["match_id"] for c in self.training}, set(self.new["cohort_ids"]["ranked_7300"]))
        for field, value in (("ranked_ids_sha256", "wrong"), ("ranked_count", 92), ("canonical_sha256", "wrong")):
            with self.subTest(field=field):
                protocol = deepcopy(self.protocol)
                protocol["models"]["new"][field] = value
                with self.assertRaises(ValueError):
                    self.evaluate([], protocol=protocol)
        with self.assertRaisesRegex(ValueError, "boundary"):
            self.evaluate([], protocol=dict(self.protocol, latest_training_end=self.last - 1))
        for policy in (dict(self.receipt["policy"], as_of=self.deadline + 1),
                       dict(self.receipt["policy"], patch="7.41g")):
            with self.assertRaises(ValueError):
                self.evaluate([], receipt=dict(self.receipt, policy=policy))
        same = self.card(8, self.last + 10)
        with self.assertRaisesRegex(ValueError, "unique match IDs"):
            self.evaluate([same, deepcopy(same)])

    def test_offline_cli_excludes_all_93_training_matches(self):
        folder = study.EXAMPLES / "ranked-collection-2026-11-18"
        with TemporaryDirectory() as directory:
            out = Path(directory) / "score.json"
            args = ["compare_ranked_93", "--capture", str(folder), "--out", str(out)]
            with patch("sys.argv", args), redirect_stdout(StringIO()):
                study.main()
            report = study.read_json(out)
        self.assertEqual(report["excluded"]["in_model_training"], 93)
        self.assertEqual(report["evaluation"]["primary"]["players"], 0)
        self.assertEqual(report["evaluation"]["overlap_diagnostic"]["match_ids"], [])
