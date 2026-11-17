import copy
import unittest
from unittest.mock import patch

from pro_replay.ml_experiments import RECIPES, TARGETS, canonical_hash
from pro_replay.ranked_selection import select_ranked
from tests.test_ml_experiments import games


class RankedDomainSelectionTests(unittest.TestCase):
    def setUp(self):
        self.games = games(3, 20)
        for game in self.games:
            game["league_id"] = 0
            for row in game["rows"]:
                row["at20"] = {key: row["at10"][key] + gain
                               for key, gain in zip(TARGETS, (20, 200, 300))}
        # Deliberately simple portable predictors exercise selection independently
        # of fitting or optional sklearn. Recipe metadata remains fixed.
        self.bundles = {recipe["name"]: {
            "format": "economy-predictor-1", "recipe": copy.deepcopy(recipe),
            "estimator": {"kind": "rank_median"},
            "baseline": {str(rank): [25, 250, 350] for rank in ("all", 1, 2, 3, 4, 5)},
            "train_match_ids": [1, 2], "train_last_end": 100,
            "hero_counts": {str(hero): 25 for hero in range(1, 11)}, "farm_bin_edges": [25, 35],
        } for recipe in RECIPES}

    def test_unchanged_candidates_are_ranked_without_fitting_and_receipt_is_hashed(self):
        chosen = "ridge_rich_100"
        self.bundles[chosen]["baseline"] = {str(rank): [20, 200, 300] for rank in ("all", 1, 2, 3, 4, 5)}
        original_bundles, original_games = copy.deepcopy(self.bundles), copy.deepcopy(self.games)
        with patch("pro_replay.ml_experiments.fit_candidate", side_effect=AssertionError("Must never fit")):
            result = select_ranked(self.bundles, self.games)
        self.assertEqual(result["winner"], chosen)
        self.assertFalse(result["fit_performed"])
        self.assertEqual(self.bundles, original_bundles)
        self.assertEqual(self.games, original_games)
        self.assertEqual(result["candidate_sha256"][chosen], canonical_hash(self.bundles[chosen]))
        digest = result.pop("sha256")
        self.assertEqual(digest, canonical_hash(result))
        self.assertIn("development data only", result["selection_scope"])
        self.assertIn("not independent", result["uncertainty"]["limitation"])
        self.assertEqual(result["uncertainty"]["absolute_error_p90"], {key: 0 for key in TARGETS})

    def test_ties_follow_original_recipe_order_not_mapping_or_input_game_order(self):
        expected = select_ranked(self.bundles, self.games)
        reversed_mapping = dict(reversed(list(self.bundles.items())))
        actual = select_ranked(reversed_mapping, list(reversed(self.games)))
        self.assertEqual(actual, expected)
        self.assertEqual(actual["winner"], RECIPES[0]["name"])
        self.assertTrue(all(abs(row["score"] - 1) < 1e-12 for row in actual["leaderboard"]))

    def test_training_match_overlap_or_unfinished_training_is_rejected(self):
        self.bundles["rank_median"]["train_match_ids"] = [self.games[0]["match_id"], 2]
        with self.assertRaisesRegex(ValueError, "training match"):
            select_ranked(self.bundles, self.games)
        self.bundles["rank_median"]["train_match_ids"] = [1, 2]
        self.bundles["rank_median"]["train_last_end"] = self.games[0]["start"] + 1
        with self.assertRaisesRegex(ValueError, "before a training match finished"):
            select_ranked(self.bundles, self.games)

    def test_different_training_sets_and_changed_or_missing_recipes_are_rejected(self):
        altered = copy.deepcopy(self.bundles)
        altered["ridge_legacy"]["train_match_ids"] = [1, 3]
        with self.assertRaisesRegex(ValueError, "same original"):
            select_ranked(altered, self.games)
        altered = copy.deepcopy(self.bundles)
        altered["ridge_rich_100"]["recipe"]["alpha"] = 1000
        with self.assertRaisesRegex(ValueError, "preregistered recipe"):
            select_ranked(altered, self.games)
        altered = copy.deepcopy(self.bundles)
        altered.pop("boosting_huber")
        with self.assertRaisesRegex(ValueError, "exactly the twelve"):
            select_ranked(altered, self.games)

    def test_only_nonempty_unique_ranked_development_is_accepted(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            select_ranked(self.bundles, [])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            select_ranked(self.bundles, self.games + [self.games[0]])
        altered = copy.deepcopy(self.games)
        altered[0]["league_id"] = 77
        with self.assertRaisesRegex(ValueError, "ranked development games"):
            select_ranked(self.bundles, altered)


if __name__ == "__main__":
    unittest.main()
