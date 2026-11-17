import copy
import importlib.util
import json
import unittest

from pro_replay import item_experiments as experiments


def row(index, hero=1, label="manta"):
    return {"match_id": index + 1000, "account_id": 77, "start_time": 10000 + index * 60,
            "hero_id": hero, "patch": 60, "lh_10": 30 + index, "gold_10": 2000 + index * 30,
            "xp_10": 2100 + index * 20, "purchase_log_available": True,
            "core_items": [{"key": "power_treads", "time": 590}, {"key": label, "time": 850}]}


class ItemExperimentTests(unittest.TestCase):
    def test_same_timestamp_never_straddles_split(self):
        rows = [row(i) for i in range(40)]
        rows[31]["start_time"] = rows[32]["start_time"]
        samples, _ = experiments.prepare_rows(rows)
        train, test = experiments.temporal_split(samples, 0.8)
        self.assertEqual(len(train), 31)
        self.assertLess(max(p[0]["start_time"] for p in train), min(p[0]["start_time"] for p in test))
        for fitting, validation in experiments.validation_folds(train):
            self.assertLess(max(p[0]["start_time"] for p in fitting),
                            min(p[0]["start_time"] for p in validation))

    def test_rejects_mixed_players_and_counts_exclusions(self):
        good = row(1)
        bad = row(2)
        bad["gold_10"] = None
        ambiguity = row(3)
        ambiguity["core_items"].append({"key": "blink", "time": 850})
        valid, coverage = experiments.prepare_rows([good, good, bad, ambiguity, {**row(4), "synthetic": True}])
        self.assertEqual(len(valid), 1)
        self.assertEqual(coverage["excluded"], {"duplicate_match": 1, "missing_10_minute_snapshot": 1,
                                               "ambiguous_first_purchase": 1, "demo_row": 1})
        with self.assertRaisesRegex(ValueError, "mix accounts"):
            experiments.prepare_rows([good, {**row(5), "account_id": 78}])

    def test_history_and_scaling_use_training_and_pre10_only(self):
        train = [(row(1), "manta"), (row(2, hero=2, label="blink"), "blink")]
        prep = experiments._preprocess(train, history=True)
        example = row(3)
        original = experiments._vector(example, prep)
        example["core_items"].extend([{"key": "power_treads", "time": 601},
                                      {"key": "blink", "time": 800}])
        example["win"] = True
        example["final_gold"] = 999999
        self.assertEqual(original, experiments._vector(example, prep))
        self.assertNotIn("blink", prep["history"])
        self.assertNotIn("99", prep["categories"]["hero_id"])
        example["core_items"].append({"key": "power_treads", "time": 600})
        self.assertNotEqual(original, experiments._vector(example, prep))

    def test_unknown_hero_falls_back_to_smoothed_prior(self):
        samples = [(row(0), "manta"), (row(1), "manta"), (row(2, 2, "blink"), "blink")]
        model = experiments.fit_item_model(samples, experiments.RECIPES[1])
        prediction = experiments.item_probabilities(model, row(3, 999))
        self.assertAlmostEqual(sum(prediction.values()), 1)
        self.assertEqual(prediction, dict(zip(model["classes"], model["prior"])))
        self.assertIsNone(experiments.predict_item(model, {**row(3), "xp_10": None}))
        portable_prediction = experiments.predict_item(model, row(3, 999))
        self.assertTrue(portable_prediction["unseen_hero"])
        self.assertEqual(portable_prediction["training_hero_examples"], 0)
        self.assertFalse(portable_prediction["probability_is_calibrated"])
        self.assertIn("No training matches", portable_prediction["warning"])
        self.assertIn("Only 2 training matches", experiments.predict_item(model, row(3))["warning"])

    def test_pairing_rejects_mismatched_targets(self):
        first = [{"match_id": 1, "actual": "manta", "predicted": "blink"}]
        with self.assertRaisesRegex(ValueError, "target labels"):
            experiments.paired_accuracy_difference(first, [{**first[0], "actual": "blink"}])

    def test_future_labels_do_not_change_selected_recipe_or_training(self):
        rows = [row(i, 1 if i % 2 else 2, "manta" if i % 2 else "blink") for i in range(50)]
        first = experiments.run_item_experiment(rows, experiments.RECIPES[:3])
        altered = copy.deepcopy(rows)
        for sample in altered[40:]:
            sample["core_items"][-1]["key"] = "shivas_guard"
        second = experiments.run_item_experiment(altered, experiments.RECIPES[:3])
        self.assertEqual(first["report"]["selected_recipe"], second["report"]["selected_recipe"])
        self.assertEqual(first["model"], second["model"])
        self.assertEqual(second["report"]["comparison"][0]["legacy_test"]["unseen_labels"], 10)
        self.assertGreater(second["report"]["comparison"][0]["legacy_test"]["log_loss"], 20)

    def test_metrics_include_unseen_labels_and_conditional_confidence(self):
        predictions = [
            {"actual": "manta", "predicted": "manta", "top3": ["manta", "blink"], "confidence": 0.8,
             "actual_probability": 0.8, "brier": 0.08, "unseen_label": False},
            {"actual": "blink", "predicted": "manta", "top3": ["manta", "blink"], "confidence": 0.5,
             "actual_probability": 0.5, "brier": 0.5, "unseen_label": False},
        ]
        metrics = experiments.classification_metrics(predictions)
        self.assertEqual(metrics["accuracy"], 0.5)
        self.assertEqual(metrics["top3_accuracy"], 1)
        self.assertAlmostEqual(metrics["macro_f1"], 1 / 3)
        self.assertEqual(metrics["confident_coverage"], 0.5)
        self.assertEqual(metrics["confident_accuracy"], 1)

    @unittest.skipUnless(importlib.util.find_spec("sklearn"), "optional ML dependencies are absent")
    def test_portable_classifier_probabilities_match_sklearn(self):
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        for class_count in (2, 3):
            labels = ("manta", "blink", "shivas_guard")[:class_count]
            train = [(row(i, i % class_count + 1, labels[i % class_count]), labels[i % class_count])
                     for i in range(36)]
            for recipe in (experiments.RECIPES[5], experiments.RECIPES[6], experiments.RECIPES[7]):
                model = experiments.fit_item_model(train, recipe)
                model = json.loads(json.dumps(model))
                features = [experiments._vector(r, model["preprocessing"]) for r, _ in train]
                if recipe["kind"] == "logistic":
                    estimator = LogisticRegression(C=recipe["C"], max_iter=500, random_state=41)
                else:
                    estimator = RandomForestClassifier(n_estimators=80, max_depth=6, min_samples_leaf=3,
                                                       random_state=41, n_jobs=1)
                estimator.fit(features, [label for _, label in train])
                for sample in [row(5, 1), row(10, 2), row(99, 999)]:
                    expected = estimator.predict_proba([experiments._vector(sample, model["preprocessing"])])[0]
                    actual = experiments.item_probabilities(model, sample)
                    for key, probability in zip(estimator.classes_, expected):
                        self.assertAlmostEqual(actual[key], probability, places=10)


if __name__ == "__main__":
    unittest.main()
