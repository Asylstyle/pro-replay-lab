import copy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from pro_replay import ml_benchmark
from pro_replay.coach_features import game_features
from pro_replay.coach_model import assess_player, evaluate_ranked, train_coach_model
from pro_replay.ml_experiments import RECIPES, canonical_hash, fit_candidate, predict_bundle
from tests.test_coach_model import replay


def ranked(index):
    raw = dict(replay(index), leagueid=0, lobby_type=7, game_mode=22, human_players=10)
    for player in raw["players"]:
        player["leaver_status"] = 0
    return raw


class RankedWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.pro_raw = [replay(i) for i in range(10)]
        self.legacy = train_coach_model(self.pro_raw)
        development = [game_features(raw)[0] for raw in self.pro_raw[:8]]
        self.predictor = fit_candidate("rank_median", development)
        self.predictor["recipe"] = copy.deepcopy(next(r for r in RECIPES if r["name"] == "ridge_base_100"))
        self.predictor["baseline"] = {str(rank): [999, 9999, 9999] for rank in ("all", 1, 2, 3, 4, 5)}
        self.candidates = {"ridge_base_100": self.predictor,
                           "ridge_legacy": fit_candidate("ridge_legacy", development),
                           "rank_median": fit_candidate("rank_median", development)}
        self.uncertainty = {"method": "ranked_development_absolute_residual_p90", "nominal_coverage": 0.9,
                            "absolute_error_p90": {"lh": 10, "gold": 100, "xp": 150},
                            "limitation": "Descriptive, not guaranteed coverage."}
        self.selected = {"winner": "ridge_base_100", "development_match_ids": [120, 130],
                         "candidate_sha256": {name: canonical_hash(bundle) for name, bundle in self.candidates.items()},
                         "uncertainty": self.uncertainty, "test_kind": "retrospective", "sha256": "frozen-selection"}
        self.raw_test = [ranked(40), ranked(41)]
        self.manifest = {
            "snapshot_sha256": hashlib.sha256(b"frozen-test").hexdigest(),
            "match_ids": [140, 141], "matches": 2,
            "sampling_policy": {"window_start_inclusive": self.raw_test[0]["start_time"],
                                "window_end_exclusive": replay(42)["start_time"],
                                "excluded_prior_match_ids": [120, 130],
                                "original_400_pro_training_latest_end": self.legacy["coverage"]["train_last_end"]}}

    def _evaluate(self, *, raw_test=None, manifest=None, mutate_candidate=False, mutate_selection=False):
        out = Path("unused-ranked-workflow-output")
        selected = copy.deepcopy(self.selected)
        receipt = {"selection_sha256": canonical_hash(selected), "winner": selected["winner"],
                   "test_kind": "independent_retrospective", "release_gate": "50 matches and lower MAE on all targets"}
        if mutate_selection:
            selected["winner"] = "rank_median"
        candidates = copy.deepcopy(self.candidates)
        if mutate_candidate:
            candidates["ridge_base_100"]["baseline"]["all"][0] += 1
        sources = {
            str(out / "ranked-domain-selection.json"): selected,
            str(out / "ranked-selection-receipt.json"): receipt,
            str(ml_benchmark.LEGACY_MODEL_PATH): self.legacy,
            str(ml_benchmark.EXAMPLES / "coach-ml-legacy-ranked-snapshots.json.gz"): {"matches": [ranked(20)]},
            str(ml_benchmark.EXAMPLES / "coach-ml-training-snapshots.json.gz"): {"matches": self.pro_raw},
            str(ml_benchmark.EXAMPLES / "coach-ml-holdout-snapshots.json.gz"): {"ranked": [ranked(30)], "professional": [replay(31)]},
            str(ml_benchmark.EXAMPLES / "coach-ml-ranked-retrospective-snapshots.json.gz"): {"ranked": self.raw_test if raw_test is None else raw_test},
            str(ml_benchmark.EXAMPLES / "coach-ml-ranked-retrospective-manifest.json"): self.manifest if manifest is None else manifest,
        }
        sources.update({str(out / "candidates" / (name + ".json")): bundle for name, bundle in candidates.items()})
        with patch.object(ml_benchmark, "read", side_effect=lambda path: copy.deepcopy(sources[str(path)])), \
                patch.object(ml_benchmark, "save_coach_model") as save, \
                patch.object(Path, "read_bytes", return_value=b"frozen-test"), \
                patch.object(Path, "write_text"), \
                patch("pro_replay.ml_report.render_benchmark", return_value="<html></html>"):
            result = ml_benchmark.run_ranked_evaluation(out)
            model = next(call.args[0] for call in save.call_args_list if str(call.args[1]).endswith("ranked-client-model.json"))
        return result, model

    def test_published_model_keeps_professional_predictor_and_changes_only_ranked_fit(self):
        report, model = self._evaluate()
        self.assertEqual(model["fit"], self.legacy["fit"])
        self.assertEqual(model["metrics"], self.legacy["metrics"])
        core = copy.deepcopy(model["ranked_fit"])
        self.assertEqual(core.pop("uncertainty"), self.uncertainty)
        self.assertEqual(core, self.predictor)
        self.assertEqual(model["test_kind"], "independent_retrospective")
        self.assertIn("not forward-time", report["limitations"][0])
        expected_pro = assess_player(self.legacy, replay(50), 0)
        actual_pro = assess_player(model, replay(50), 0)
        self.assertEqual(actual_pro["metrics"], expected_pro["metrics"])
        self.assertIsNone(actual_pro["uncertainty_method"])
        self.assertTrue(all("interval" not in m for m in actual_pro["metrics"]))
        actual_ranked = assess_player(model, ranked(50), 0)
        row = game_features(ranked(50), ranked=True)[0]["rows"][0]
        expected_ranked = predict_bundle(model["ranked_fit"], row)
        self.assertEqual([m["expected"] for m in actual_ranked["metrics"]], [round(expected_ranked[k], 2) for k in expected_ranked])

    def test_uncertainty_is_retained_for_calibrated_base_features_but_not_for_pro(self):
        _, model = self._evaluate()
        ranked_report = assess_player(model, ranked(50), 0)
        self.assertEqual(ranked_report["uncertainty_method"], self.uncertainty["method"])
        self.assertTrue(all("interval" in metric for metric in ranked_report["metrics"]))

    def test_other_ranked_game_modes_do_not_use_all_pick_model(self):
        _, model = self._evaluate()
        unsupported = ranked(50)
        unsupported["game_mode"] = 2
        expected = assess_player(self.legacy, unsupported, 0)
        actual = assess_player(model, unsupported, 0)
        self.assertEqual(actual["metrics"], expected["metrics"])
        self.assertEqual(actual["error_population"], "professional")

    def test_changed_selection_or_model_fails_before_test_scoring(self):
        with self.assertRaisesRegex(ValueError, "selection changed"):
            self._evaluate(mutate_selection=True)
        with self.assertRaisesRegex(ValueError, "candidate changed"):
            self._evaluate(mutate_candidate=True)

    def test_registered_window_and_match_ids_are_enforced(self):
        altered = copy.deepcopy(self.manifest)
        altered["sampling_policy"]["window_end_exclusive"] = self.raw_test[1]["start_time"]
        with self.assertRaises(ValueError):
            self._evaluate(manifest=altered)
        altered = copy.deepcopy(self.manifest)
        altered["match_ids"] = [140, 999]
        with self.assertRaises(ValueError):
            self._evaluate(manifest=altered)

    def test_registered_exclusions_and_disconnect_policy_are_enforced(self):
        altered = copy.deepcopy(self.manifest)
        altered["sampling_policy"]["excluded_prior_match_ids"].append(140)
        with self.assertRaises(ValueError):
            self._evaluate(manifest=altered)
        changed = copy.deepcopy(self.raw_test)
        changed[0]["players"][0]["leaver_status"] = 1
        with self.assertRaises(ValueError):
            self._evaluate(raw_test=changed)
        changed = copy.deepcopy(self.raw_test)
        changed[0]["human_players"] = 9
        with self.assertRaises(ValueError):
            self._evaluate(raw_test=changed)

    def test_previous_ranked_development_and_test_are_not_counted_as_new_evaluation(self):
        _, model = self._evaluate()
        result = evaluate_ranked(model, [ranked(20), ranked(30), ranked(40), ranked(50)])
        self.assertEqual(result["all_ranked"]["match_ids"], [150])
        self.assertEqual(result["excluded"]["overlap_with_previous_evaluation"], 3)


if __name__ == "__main__":
    unittest.main()
