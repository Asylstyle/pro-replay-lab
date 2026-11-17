import copy
import importlib.util
import json
import unittest

from pro_replay.coach_model import _game
from pro_replay.ml_experiments import (RECIPES, TARGETS, _interval_coverage, _preprocessing, _records,
                                       canonical_hash, chronological_folds, error_analysis,
                                       evaluate_candidates, fit_candidate, make_protocol,
                                       paired_bootstrap, predict_bundle, select_model, vectorize)
from tests.test_coach_model import replay


def games(count=30, offset=0):
    result = []
    for index in range(offset, count + offset):
        game, reason = _game(replay(index))
        assert reason is None
        game["patch"] = 57
        for player, row in enumerate(game["rows"]):
            row["features"] = {"at5_lh": row["at10"]["lh"] / 2, "missing_optional": None,
                               "lane_momentum": (index % 3) * (player + 1)}
        result.append(game)
    return result


class EconomyProtocolTests(unittest.TestCase):
    def test_grouped_chronological_folds_purge_unfinished_matches(self):
        source = games()
        source[11]["end"] = source[12]["start"] + 100
        folds = chronological_folds(source)
        self.assertEqual([len(valid) for _, valid in folds], [6, 6, 6])
        self.assertEqual(len(folds[0][0]), 11)
        validation = []
        for train, valid in folds:
            self.assertLessEqual(max(g["end"] for g in train), min(g["start"] for g in valid))
            self.assertFalse({g["match_id"] for g in train} & {g["match_id"] for g in valid})
            self.assertTrue(all(len(g["rows"]) == 10 for g in train + valid))
            validation.extend(g["match_id"] for g in valid)
        self.assertEqual(len(validation), len(set(validation)))

    def test_same_start_time_is_not_split_between_train_and_validation(self):
        source = games()
        source[11]["start"] = source[12]["start"]
        source[11]["end"] = source[12]["end"]
        train, valid = chronological_folds(source)[0]
        self.assertNotIn(source[11], train)
        self.assertIn(source[11], valid)
        self.assertIn(source[12], valid)

    def test_protocol_rejects_mutation_or_different_development_data(self):
        source = games()
        protocol = make_protocol(source)
        changed = copy.deepcopy(protocol)
        changed["recipes"][0]["name"] = "changed_after_freezing"
        with self.assertRaisesRegex(ValueError, "Protocol changed"):
            select_model(source, changed)
        source[0]["rows"][0]["at20"]["gold"] += 1
        with self.assertRaisesRegex(ValueError, "Development data differ"):
            select_model(source, protocol)

    def test_selection_has_only_development_cv_and_explicit_uncertainty_limits(self):
        source = games()
        protocol = make_protocol(source)
        protocol.pop("sha256")
        protocol["recipes"] = copy.deepcopy(list(RECIPES[:2]))
        protocol["sha256"] = canonical_hash(protocol)
        progress = []
        selection = select_model(source, protocol, progress.append)
        self.assertEqual(len(selection["folds"]), 3)
        self.assertEqual(len(progress), 12)
        self.assertIn(selection["winner"], {"rank_median", "extrapolation_10"})
        self.assertEqual(selection["uncertainty"]["games"], 18)
        self.assertEqual(selection["uncertainty"]["players"], 180)
        self.assertIn("not an independent", selection["uncertainty"]["limitation"])

    def test_preprocessing_uses_training_only_and_handles_unseen_missing_inputs(self):
        train = games(3)
        rows = [row for game in train for row in game["rows"]]
        preprocessing = _preprocessing(rows, ["at5_lh", "missing_optional"])
        saved = copy.deepcopy(preprocessing)
        query = copy.deepcopy(rows[0])
        query["hero"] = 9999
        query["features"].pop("at5_lh")
        query["features"]["missing_optional"] = 999999
        first = vectorize(query, preprocessing)
        query["at20"] = {key: 1e9 for key in TARGETS}
        query["reported_position"] = 1
        query["won"] = True
        self.assertEqual(first, vectorize(query, preprocessing))
        self.assertEqual(preprocessing, saved)
        self.assertTrue(all(value == 0 for value in first[-len(preprocessing["heroes"]):]))
        count = len(preprocessing["numeric_features"])
        self.assertEqual(first[count + 3], 1)

    def test_evaluation_rejects_training_overlap_and_temporal_overlap(self):
        train = games(5)
        fitted = fit_candidate("rank_median", train)
        with self.assertRaisesRegex(ValueError, "training match"):
            evaluate_candidates({"ridge_legacy": fitted}, {"bad": [train[0]]})
        earlier = games(1, 10)
        earlier[0]["start"] = train[0]["start"]
        with self.assertRaisesRegex(ValueError, "after all training"):
            evaluate_candidates({"ridge_legacy": fitted}, {"bad": earlier})

    def test_duplicate_ids_and_incomplete_rosters_are_rejected(self):
        source = games(2)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            fit_candidate("rank_median", source + [source[0]])
        source[0]["rows"].pop()
        with self.assertRaisesRegex(ValueError, "ten-player"):
            fit_candidate("rank_median", source)


class EconomyEvaluationTests(unittest.TestCase):
    def test_bootstrap_resamples_whole_paired_matches_and_is_reproducible(self):
        fitted = fit_candidate("rank_median", games(4))
        records = _records(fitted, games(4, 10))
        reference = copy.deepcopy(records)
        for row in records:
            row["errors"] = {key: 1.0 for key in TARGETS}
        for row in reference:
            row["errors"] = {key: 3.0 for key in TARGETS}
        output = paired_bootstrap(records, reference, repetitions=50)
        self.assertEqual(output, paired_bootstrap(records, reference, repetitions=50))
        self.assertEqual(output["games"], 4)
        for key in TARGETS:
            self.assertEqual(output["targets"][key], {"mae_delta": -2.0, "ci95": [-2.0, -2.0]})
        with self.assertRaisesRegex(ValueError, "same unique players"):
            paired_bootstrap(records[:-1], reference, repetitions=50)
        with self.assertRaisesRegex(ValueError, "all ten players"):
            paired_bootstrap(records[:-1], reference[:-1], repetitions=50)

    def test_error_slices_use_training_support_and_return_traceable_examples(self):
        fitted = fit_candidate("rank_median", games(4))
        held = games(1, 10)
        held[0]["rows"][0]["hero"] = 9999
        records = _records(fitted, held)
        output = error_analysis(fitted, records)
        self.assertEqual(output["slices"]["hero_train_support/unseen_0"]["players"], 1)
        self.assertEqual(output["slices"]["hero_train_support/rare_1_19"]["players"], 9)
        self.assertTrue(output["slices"]["farm_rank_10/1"]["small_sample"])
        example = output["largest_absolute_errors"]["gold"][0]
        self.assertEqual(example["match_id"], held[0]["match_id"])
        self.assertAlmostEqual(example["absolute_error"], abs(example["predicted"] - example["actual"]))

    def test_empirical_bands_report_actual_coverage_and_physical_lower_bound(self):
        fitted = fit_candidate("rank_median", games(4))
        records = _records(fitted, games(1, 10))
        fitted["uncertainty"] = {"method": "test", "nominal_coverage": 0.9,
                                 "absolute_error_p90": {key: 1e8 for key in TARGETS},
                                 "limitation": "descriptive only"}
        output = _interval_coverage(fitted, records)
        self.assertTrue(all(item["empirical_coverage"] == 1 for item in output["targets"].values()))
        self.assertTrue(all(item["mean_width"] < 2e8 for item in output["targets"].values()))


@unittest.skipUnless(importlib.util.find_spec("sklearn"), "Optional ML-training environment is not installed")
class PortableEstimatorTests(unittest.TestCase):
    def test_all_trainable_families_match_sklearn_and_roundtrip_without_future_inputs(self):
        source = games(4)
        names = {"ridge_rich_10", "random_forest", "extra_trees", "boosting_absolute", "boosting_huber"}
        for recipe in RECIPES:
            if recipe["name"] not in names:
                continue
            with self.subTest(recipe=recipe["name"]):
                small = dict(recipe)
                if "n_estimators" in small:
                    small["n_estimators"] = 4
                    small["min_samples_leaf"] = 2
                fitted = fit_candidate(small, source)
                self.assertLessEqual(fitted["export_validation"]["max_absolute_prediction_difference"], 1e-7)
                restored = json.loads(json.dumps(fitted, allow_nan=False))
                query = copy.deepcopy(source[-1]["rows"][0])
                expected = predict_bundle(fitted, query)
                query["at20"] = {key: 999999 for key in TARGETS}
                query["reported_position"] = 1
                query["win"] = True
                self.assertEqual(expected, predict_bundle(restored, query))
                self.assertTrue(all(expected[key] >= query["at10"][key] for key in TARGETS))


if __name__ == "__main__":
    unittest.main()
