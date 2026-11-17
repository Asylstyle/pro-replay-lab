"""Published client model routing, reproducibility and future-label isolation."""

import copy
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean
import unittest

from pro_replay.coach_model import LEGACY_MODEL_PATH, assess_player, evaluate_ranked, load_coach_model
from pro_replay.impact import SLOTS


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class PublishedClientTests(unittest.TestCase):
    def test_registered_policy_bytes_and_objects_match_collection_manifests(self):
        for name in ("coach-ml-holdout", "coach-ml-ranked-final", "coach-ml-ranked-retrospective"):
            data = (EXAMPLES / (name + "-selection.json")).read_bytes()
            manifest = json.loads((EXAMPLES / (name + "-manifest.json")).read_text(encoding="utf8"))
            self.assertEqual(hashlib.sha256(data).hexdigest(), manifest["sampling_policy_sha256"])
            self.assertEqual(json.loads(data), manifest["sampling_policy"])

    @classmethod
    def setUpClass(cls):
        path = EXAMPLES / "ranked-client-model.json"
        if not path.exists():
            raise unittest.SkipTest("Client model has not passed its release gate yet.")
        cls.model = load_coach_model(path)
        cls.legacy = load_coach_model(LEGACY_MODEL_PATH)
        with gzip.open(EXAMPLES / "coach-ml-ranked-retrospective-snapshots.json.gz", "rt", encoding="utf8") as src:
            cls.games = json.load(src)["ranked"]

    def test_published_predictions_reproduce_independent_test_errors(self):
        self.assertTrue(self.model["release_gate_passed"])
        self.assertEqual(load_coach_model(), self.model)
        errors = {k: [] for k in ("lh", "gold", "xp")}
        for raw in self.games:
            for slot in SLOTS:
                report = assess_player(self.model, raw, slot)
                self.assertEqual(report["cohort"], "holdout")
                self.assertEqual(report["feature_version"], 2)
                for metric in report["metrics"]:
                    errors[metric["key"]].append(abs(metric["actual"] - metric["expected"]))
                    self.assertIn("interval", metric)
        for key, values in errors.items():
            self.assertAlmostEqual(mean(values), self.model["ranked_metrics"][key]["mae"], delta=.005)

    def test_future_labels_and_outcomes_never_change_client_forecast(self):
        raw = self.games[0]
        altered = copy.deepcopy(raw)
        altered["radiant_win"] = not raw.get("radiant_win")
        for player in altered["players"]:
            player["position_est"] = 1
            player["kills"] = 999
            for index, second in enumerate(player["times"]):
                if second > 600:
                    for key in ("lh_t", "gold_t", "xp_t"):
                        player[key][index] = 999999
        before = assess_player(self.model, raw, 0)
        after = assess_player(self.model, altered, 0)
        self.assertEqual([m["expected"] for m in before["metrics"]], [m["expected"] for m in after["metrics"]])
        self.assertEqual([m["interval"] for m in before["metrics"]], [m["interval"] for m in after["metrics"]])

    def test_professional_forecast_uses_existing_professional_model(self):
        with gzip.open(EXAMPLES / "coach-pro-snapshots.json.gz", "rt", encoding="utf8") as src:
            raw = json.load(src)[-1]
        self.assertEqual(self.model["fit"], self.legacy["fit"])
        new = assess_player(self.model, raw, 0)
        old = assess_player(self.legacy, raw, 0)
        self.assertEqual(new["metrics"], old["metrics"])
        self.assertEqual(new["feature_version"], 1)

    def test_reused_ranked_development_cannot_be_reported_as_new_evidence(self):
        with gzip.open(EXAMPLES / "coach-ml-holdout-snapshots.json.gz", "rt", encoding="utf8") as src:
            prior = json.load(src)["ranked"]
        self.assertEqual(assess_player(self.model, prior[0], 0)["cohort"], "development")
        self.assertEqual(evaluate_ranked(self.model, prior)["all_ranked"]["games"], 0)
        self.assertFalse(set(self.model["ranked_development_match_ids"]) & set(self.model["fresh_test_match_ids"]))
