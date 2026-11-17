"""Regression checks for the boundary between frozen training and evaluation."""

import copy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from pro_replay import ml_benchmark
from pro_replay.coach_features import game_features
from pro_replay.coach_model import assess_player, evaluate_ranked, train_coach_model
from pro_replay.ml_experiments import canonical_hash, fit_candidate
from tests.test_coach_model import replay


class BenchmarkIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.raw = [replay(i) for i in range(10)]
        self.legacy = train_coach_model(self.raw)
        self.development = [game_features(raw)[0] for raw in self.raw[:8]]
        self.predictor = fit_candidate("rank_median", self.development)
        self.old_ranked = [dict(replay(20), leagueid=0, lobby_type=7, game_mode=22)]
        self.fresh = {"professional": [replay(30)],
                      "ranked": [dict(replay(40), leagueid=0, lobby_type=7, game_mode=22)]}

    def _evaluate(self, raw_pro=None, old_ranked=None, fresh=None, snapshot_hash=None):
        out = Path("unused-benchmark-test-output")
        selection = {"winner": "rank_median", "uncertainty": {"absolute_error_p90": {"lh": 10, "gold": 100, "xp": 100}}}
        candidates = {"rank_median": self.predictor, "ridge_legacy": fit_candidate("ridge_legacy", self.development)}
        receipt = {"selection_sha256": canonical_hash(selection), "protocol_sha256": "test-protocol",
                   "candidate_sha256": {name: canonical_hash(bundle) for name, bundle in candidates.items()},
                   "release_gate": "All targets improve with at least fifty fresh ranked matches."}
        sources = {
            str(out / "economy-selection.json"): selection,
            str(out / "economy-selection-receipt.json"): receipt,
            str(ml_benchmark.LEGACY_MODEL_PATH): self.legacy,
            str(ml_benchmark.EXAMPLES / "coach-ml-training-snapshots.json.gz"): {"matches": self.raw if raw_pro is None else raw_pro},
            str(ml_benchmark.EXAMPLES / "coach-ml-legacy-ranked-snapshots.json.gz"): {"matches": self.old_ranked if old_ranked is None else old_ranked},
            str(ml_benchmark.EXAMPLES / "coach-ml-holdout-snapshots.json.gz"): self.fresh if fresh is None else fresh,
            str(ml_benchmark.EXAMPLES / "coach-ml-holdout-manifest.json"): {
                "snapshot_sha256": snapshot_hash or hashlib.sha256(b"frozen-test-corpus").hexdigest(),
                "sampling_policy": {"strict_start_after": self.legacy["coverage"]["train_last_end"]}},
        }
        sources.update({str(out / "candidates" / (name + ".json")): bundle for name, bundle in candidates.items()})
        with patch.object(ml_benchmark, "read", side_effect=lambda path: copy.deepcopy(sources[str(path)])), \
                patch.object(ml_benchmark, "save_coach_model"), \
                patch("pro_replay.ml_report.render_benchmark", return_value="audit"), \
                patch.object(Path, "write_text"), \
                patch.object(Path, "read_bytes", return_value=b"frozen-test-corpus"):
            return ml_benchmark.run_evaluation(out)

    def test_external_evaluation_does_not_change_selected_recipe(self):
        report = self._evaluate()
        self.assertEqual(report["release_gate"]["winner"], "rank_median")
        self.assertFalse(report["release_gate"]["selection_used_external_scores"])
        self.assertFalse(report["release_gate"]["passed"])

    def test_fresh_evaluation_excludes_even_ineligible_historically_seen_match_ids(self):
        short = dict(replay(40), leagueid=0, lobby_type=7, game_mode=22, duration=1100)
        with self.assertRaisesRegex(ValueError, "historical|disjoint"):
            self._evaluate(old_ranked=self.old_ranked + [short])

    def test_missing_historical_pro_match_must_not_silently_change_benchmark(self):
        with self.assertRaisesRegex(ValueError, "(?i)historical|legacy|incomplete"):
            self._evaluate(raw_pro=self.raw[:-1])

    def test_changed_holdout_bytes_fail_the_manifest_check(self):
        with self.assertRaisesRegex(ValueError, "manifest"):
            self._evaluate(snapshot_hash="changed")

    def test_new_holdout_must_respect_registered_strict_time_boundary(self):
        changed = copy.deepcopy(self.fresh)
        changed["ranked"][0]["start_time"] = self.legacy["coverage"]["train_last_end"]
        with self.assertRaisesRegex(ValueError, "time boundary"):
            self._evaluate(fresh=changed)

    def test_fresh_evaluated_match_is_identified_as_holdout_in_player_report(self):
        model = {"format": "pro-replay-coach-2", "algorithm": "rank_median", "fit": self.predictor,
                 "coverage": self.legacy["coverage"], "metrics": self.legacy["metrics"],
                 "fresh_test_match_ids": [130]}
        self.assertEqual(assess_player(model, replay(30), 0)["cohort"], "holdout")

    def test_ranked_evaluation_does_not_recount_previous_fresh_holdout_as_unseen(self):
        model = {"format": "pro-replay-coach-2", "algorithm": "rank_median", "fit": self.predictor,
                 "coverage": self.legacy["coverage"], "metrics": self.legacy["metrics"],
                 "fresh_test_match_ids": [140]}
        report = evaluate_ranked(model, self.fresh["ranked"])
        self.assertEqual(report["all_ranked"]["games"], 0)
        self.assertEqual(sum(report["excluded"].values()), 1)


if __name__ == "__main__":
    unittest.main()
