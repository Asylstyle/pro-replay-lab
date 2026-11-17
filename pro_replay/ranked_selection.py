"""Select an unchanged pro-trained predictor for ranked clients on development data.

This second experiment deliberately reclassifies the first experiment's already
inspected ranked holdout as development data. It is not fresh evidence of model
quality. An independent test must be opened only after this result and the
candidate hashes have been saved. Retrospective tests must be named explicitly;
they do not establish forward-time performance. No estimator is fitted here.
"""

from copy import deepcopy
from statistics import mean

from .ml_experiments import (RECIPES, TARGETS, _games, _metrics, _percentile, _records,
                             canonical_hash, error_analysis)


def select_ranked(bundles, development_games, *, test_policy="future"):
    """Rank exactly the twelve existing recipes without changing their parameters.

    The caller must verify the input candidate hashes against the phase-one
    receipt. The returned receipt freezes those exact predictors and all ranked
    development rows for the second experiment. No final-test argument exists.
    """
    if test_policy not in ("future", "retrospective"):
        raise ValueError("Test policy must explicitly be future or retrospective.")
    recipes = {recipe["name"]: recipe for recipe in RECIPES}
    if set(bundles) != set(recipes):
        raise ValueError("Ranked selection requires exactly the twelve preregistered candidate recipes.")
    for name, bundle in bundles.items():
        if bundle.get("format") != "economy-predictor-1" or bundle.get("recipe") != recipes[name]:
            raise ValueError("A candidate differs from its preregistered recipe.")
    games = _games(development_games)
    if not games:
        raise ValueError("Ranked development data are empty.")
    if any(game.get("league_id") != 0 for game in games):
        raise ValueError("Domain selection requires ranked development games, not professional games.")
    identifiers = {game["match_id"] for game in games}
    training_ids = None
    for bundle in bundles.values():
        current_ids = set(bundle["train_match_ids"])
        if len(current_ids) != len(bundle["train_match_ids"]):
            raise ValueError("Candidate training match IDs are not unique.")
        if training_ids is None:
            training_ids = current_ids
        elif training_ids != current_ids:
            raise ValueError("Candidates must share the same original professional training matches.")
        if current_ids & identifiers:
            raise ValueError("Ranked development contains a candidate training match.")
        if games[0]["start"] < bundle["train_last_end"]:
            raise ValueError("Ranked development starts before a training match finished.")

    hashes = {name: canonical_hash(bundles[name]) for name in recipes}
    records = {name: _records(bundles[name], games) for name in recipes}
    reference = _metrics(records["rank_median"])
    leaderboard = []
    for name in recipes:
        metrics = _metrics(records[name])
        score = mean(metrics[key]["mae"] / max(reference[key]["mae"], 1e-12) for key in TARGETS)
        leaderboard.append({"name": name, "score": score, "metrics": metrics})
    best = leaderboard[0]
    for candidate in leaderboard[1:]:
        if candidate["score"] < best["score"] - 1e-12:
            best = candidate
    winner = best["name"]
    chosen = records[winner]
    uncertainty = {
        "method": "ranked_development_absolute_residual_p90", "nominal_coverage": 0.9,
        "absolute_error_p90": {key: _percentile([abs(row["errors"][key]) for row in chosen], 0.9)
                               for key in TARGETS},
        "players": len(chosen), "games": len(games),
        "limitation": "Descriptive bands from the same ranked development data used for model selection; not independent conformal calibration or a coverage guarantee.",
    }
    result = {
        "format": "ranked-domain-selection-1", "phase": 2,
        "winner": winner, "winner_recipe": deepcopy(recipes[winner]), "leaderboard": leaderboard,
        "selection": "mean of target MAE / rank-median MAE over all ranked development players",
        "tie_break": "original preregistered recipe order; absolute score difference <= 1e-12",
        "selection_scope": "Previously inspected ranked games, including the phase-one ranked holdout, are now development data only.",
        "fit_performed": False, "candidate_sha256": hashes,
        "development_match_ids": [game["match_id"] for game in games],
        "development_sha256": canonical_hash(games),
        "development": {"games": len(games), "players": len(chosen),
                        "first_start": games[0]["start"], "last_end": max(game["end"] for game in games),
                        "patches": sorted({str(game.get("patch")) for game in games}),
                        "rank_tiers": sorted({str(game.get("rank_tier")) for game in games})},
        "uncertainty": uncertainty, "development_error_analysis": error_analysis(bundles[winner], chosen),
        "test_kind": test_policy,
        "final_test_policy": ("Freeze this result and candidate hashes before opening strictly newer, disjoint ranked games; do not tune or switch candidates on those final scores."
                              if test_policy == "future" else
                              "Freeze this result and candidate hashes before opening unseen retrospective ranked games from the separately registered window. This is an independent historical check, not forward-time evidence. Never retune or change the selected recipe on these scores."),
        "limitations": [
            "Development scores are selection-biased and are not independent evidence of improved accuracy.",
            "The first experiment and its failed transfer result remain unchanged.",
            "Pro-trained resource forecasts are not causal impact scores or optimal-action labels.",
        ],
    }
    result["sha256"] = canonical_hash(result)
    return result
