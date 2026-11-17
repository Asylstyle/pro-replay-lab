"""Preregistered, match-grouped economy experiments with portable JSON inference.

Only ``fit_candidate`` needs numpy/scikit-learn. Inference and evaluation use the
standard library. Rows contain the existing at10/at20/hero/rank contract and an
optional ``features`` mapping extracted strictly at or before minute ten.
"""

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math
import random
from statistics import mean, median, pstdev
import struct

from . import coach_model


TARGETS = ("lh", "gold", "xp")
SEED = 20261117
RECIPES = (
    {"name": "rank_median", "family": "rank_median", "features": "base"},
    {"name": "extrapolation_10", "family": "extrapolation", "features": "base"},
    {"name": "ridge_legacy", "family": "legacy", "features": "base", "alpha": 10.0},
    {"name": "ridge_base_100", "family": "ridge", "features": "base", "alpha": 100.0},
    {"name": "ridge_rich_1", "family": "ridge", "features": "rich", "alpha": 1.0},
    {"name": "ridge_rich_10", "family": "ridge", "features": "rich", "alpha": 10.0},
    {"name": "ridge_rich_100", "family": "ridge", "features": "rich", "alpha": 100.0},
    {"name": "random_forest", "family": "random_forest", "features": "rich",
     "n_estimators": 80, "max_depth": 12, "min_samples_leaf": 8, "max_features": 0.8},
    {"name": "extra_trees", "family": "extra_trees", "features": "rich",
     "n_estimators": 96, "max_depth": 14, "min_samples_leaf": 5, "max_features": 1.0},
    {"name": "extra_trees_base", "family": "extra_trees", "features": "base",
     "n_estimators": 96, "max_depth": 14, "min_samples_leaf": 5, "max_features": 1.0},
    {"name": "boosting_huber", "family": "boosting", "features": "rich",
     "n_estimators": 100, "max_depth": 2, "min_samples_leaf": 15,
     "learning_rate": 0.05, "loss": "huber"},
    {"name": "boosting_absolute", "family": "boosting", "features": "rich",
     "n_estimators": 100, "max_depth": 2, "min_samples_leaf": 15,
     "learning_rate": 0.05, "loss": "absolute_error"},
)


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _games(games):
    ordered = sorted(games, key=lambda g: (g["start"], g["match_id"]))
    if len({g["match_id"] for g in ordered}) != len(ordered):
        raise ValueError("Duplicate match IDs are not independent observations.")
    for game in ordered:
        if game["end"] < game["start"] or len(game["rows"]) != 10:
            raise ValueError("Each eligible game must contain its complete ten-player roster.")
        for row in game["rows"]:
            if not row.get("hero") or row.get("rank") not in range(1, 6):
                raise ValueError("Missing hero or minute-ten team farm rank.")
            for key in TARGETS:
                before, after = row.get("at10", {}).get(key), row.get("at20", {}).get(key)
                if not _finite(before) or not _finite(after) or before < 0 or after < before:
                    raise ValueError("Exact, finite cumulative minute-ten and minute-twenty labels are required.")
    return ordered


def chronological_folds(development_games):
    """Three expanding windows, purging games unfinished at validation start."""
    games = _games(development_games)
    if len(games) < 20:
        raise ValueError("At least twenty development matches are required.")
    result = []
    boundaries = [len(games) * numerator // 5 for numerator in (2, 3, 4, 5)]
    for begin, finish in zip(boundaries, boundaries[1:]):
        cutoff = games[begin]["start"]
        # Equal start times cannot be split across a train/validation boundary.
        upper = games[finish]["start"] if finish < len(games) else None
        train = [g for g in games[:begin] if g["end"] <= cutoff and g["start"] < cutoff]
        valid = [g for g in games if g["start"] >= cutoff and (upper is None or g["start"] < upper)]
        if not train or not valid:
            raise ValueError("A chronological fold is empty after time-overlap purging.")
        result.append((train, valid))
    validation_ids = [g["match_id"] for _, val in result for g in val]
    if len(validation_ids) != len(set(validation_ids)):
        raise ValueError("Validation folds overlap; timestamps do not permit these boundaries.")
    return result


def make_protocol(development_games):
    """Freeze this object before fetching/opening the new external holdout."""
    games = _games(development_games)
    folds = chronological_folds(games)
    names = sorted({str(k) for g in games for r in g["rows"] for k in r.get("features", {})})
    protocol = {
        "format": "economy-experiment-protocol-1", "seed": SEED,
        "targets": list(TARGETS), "input_second": 600, "target_second": 1200,
        "feature_names": names, "recipes": deepcopy(list(RECIPES)),
        "selection": "mean of target MAE / rank-median MAE, pooled across rolling validation matches",
        "tie_break": "recipe order; absolute score difference <= 1e-12",
        "development_match_ids": [g["match_id"] for g in games],
        "development_sha256": canonical_hash(games),
        "folds": [{"train_match_ids": [g["match_id"] for g in train],
                   "validation_match_ids": [g["match_id"] for g in valid],
                   "train_last_end": max(g["end"] for g in train),
                   "validation_first_start": min(g["start"] for g in valid)} for train, valid in folds],
        "external_policy": "Legacy pro/ranked sets are descriptive only; fresh holdout is evaluated once after selection.",
        "bootstrap": {"unit": "whole match", "repetitions": 2000, "confidence": 0.95, "seed": SEED},
        "limitations": ["Economy forecasts describe associations, not causal impact or optimal actions.",
                        "Only complete games lasting at least twenty minutes are represented.",
                        "Farm rank at minute ten is not an independently verified playing position."],
    }
    protocol["sha256"] = canonical_hash(protocol)
    return protocol


def _numeric_value(row, name):
    value = row["at10"].get(name[5:]) if name.startswith("at10:") else row.get("features", {}).get(name[6:])
    return float(value) if _finite(value) else None


def _preprocessing(rows, feature_names):
    names = ["at10:" + key for key in TARGETS] + ["extra:" + key for key in feature_names]
    prep = {"numeric_features": names, "median": [], "center": [], "scale": [],
            "heroes": sorted({int(row["hero"]) for row in rows}), "missing_indicators": True}
    for name in names:
        values = [_numeric_value(row, name) for row in rows]
        present = [v for v in values if v is not None]
        fill = median(present) if present else 0.0
        complete = [fill if v is None else v for v in values]
        prep["median"].append(fill)
        prep["center"].append(mean(complete))
        prep["scale"].append(pstdev(complete) or 1.0)
    return prep


def vectorize(row, preprocessing):
    """Read no future labels, outcome, account identity or retrospective role."""
    values = [_numeric_value(row, name) for name in preprocessing["numeric_features"]]
    output = [((preprocessing["median"][i] if value is None else value) - preprocessing["center"][i])
              / preprocessing["scale"][i] for i, value in enumerate(values)]
    if preprocessing.get("missing_indicators"):
        output.extend(float(value is None) for value in values)
    output.extend(float(row.get("rank") == rank) for rank in range(1, 6))
    output.extend(float(row.get("hero") == hero) for hero in preprocessing["heroes"])
    return output


def _tree(tree):
    return {"feature": tree.feature.tolist(), "threshold": tree.threshold.tolist(),
            "left": tree.children_left.tolist(), "right": tree.children_right.tolist(),
            "value": [v.reshape(-1).tolist() for v in tree.value]}


def fit_candidate(recipe, games, feature_names=None):
    """Fit a fixed recipe on the supplied training matches and export its predictor."""
    if isinstance(recipe, str):
        recipe = next((r for r in RECIPES if r["name"] == recipe), None)
        if recipe is None:
            raise ValueError("Unknown preregistered recipe.")
    games = _games(games)
    if not games:
        raise ValueError("No training matches.")
    rows = [row for game in games for row in game["rows"]]
    gains = [[row["at20"][key] - row["at10"][key] for key in TARGETS] for row in rows]
    groups = defaultdict(list)
    for row, gain in zip(rows, gains):
        groups[str(row["rank"])].append(gain)
        groups["all"].append(gain)
    baseline = {rank: [median(column) for column in zip(*group)] for rank, group in groups.items()}
    farm = sorted(row["at10"]["lh"] for row in rows)
    result = {"format": "economy-predictor-1", "recipe": deepcopy(recipe), "targets": list(TARGETS),
              "baseline": baseline, "hero_counts": dict(Counter(str(row["hero"]) for row in rows)),
              "train_match_ids": [g["match_id"] for g in games],
              "train_first_start": games[0]["start"], "train_last_end": max(g["end"] for g in games),
              "farm_bin_edges": [farm[len(farm) // 3], farm[2 * len(farm) // 3]]}
    family = recipe["family"]
    if family in ("rank_median", "extrapolation"):
        result["estimator"] = {"kind": family}
        return result
    if family == "legacy":
        result["estimator"] = {"kind": "legacy", "fit": coach_model._fit(rows)}
        return result
    # Training-only imports keep ordinary coaching independent of sklearn/numpy.
    import numpy as np
    import sklearn
    from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import Ridge

    feature_names = sorted(feature_names if feature_names is not None else
                           {str(k) for row in rows for k in row.get("features", {})})
    prep = _preprocessing(rows, feature_names if recipe.get("features") == "rich" else [])
    result["preprocessing"] = prep
    result["training_versions"] = {"numpy": np.__version__, "scikit_learn": sklearn.__version__}
    x = np.asarray([vectorize(row, prep) for row in rows], dtype=np.float64)
    y = np.asarray(gains, dtype=np.float64)
    center, scale = y.mean(axis=0), y.std(axis=0)
    scale[scale == 0] = 1.0
    result["target_scaling"] = {"center": center.tolist(), "scale": scale.tolist()}
    y = (y - center) / scale
    if family == "ridge":
        model = Ridge(alpha=recipe["alpha"], solver="cholesky").fit(x, y)
        estimator = {"kind": "linear", "coefficients": model.coef_.tolist(),
                     "intercept": model.intercept_.tolist()}
        reference_predictions = model.predict(x)
    elif family in ("random_forest", "extra_trees"):
        cls = RandomForestRegressor if family == "random_forest" else ExtraTreesRegressor
        params = {k: recipe[k] for k in ("n_estimators", "max_depth", "min_samples_leaf", "max_features")}
        model = cls(**params, random_state=SEED, n_jobs=1).fit(x, y)
        estimator = {"kind": "forest", "float32_inputs": True,
                     "trees": [_tree(t.tree_) for t in model.estimators_]}
        reference_predictions = model.predict(x)
    elif family == "boosting":
        params = {k: recipe[k] for k in ("n_estimators", "max_depth", "min_samples_leaf", "learning_rate", "loss")}
        outputs = []
        reference_columns = []
        for target in range(len(TARGETS)):
            model = GradientBoostingRegressor(**params, random_state=SEED).fit(x, y[:, target])
            reference_columns.append(model.predict(x))
            outputs.append({"initial": float(model.init_.constant_.reshape(-1)[0]),
                            "learning_rate": recipe["learning_rate"],
                            "trees": [_tree(t[0].tree_) for t in model.estimators_]})
        estimator = {"kind": "boosting", "float32_inputs": True, "outputs": outputs}
        reference_predictions = np.asarray(reference_columns).T
    else:
        raise ValueError("Unsupported recipe family.")
    result["estimator"] = estimator
    # Verify the portable evaluator against the training library before saving.
    # These are parity checks, never model-quality/selection measurements.
    indexes = sorted(set(np.linspace(0, len(rows) - 1, min(64, len(rows)), dtype=int).tolist()))
    max_error = 0.0
    for i in indexes:
        portable = predict_bundle(result, rows[i])
        expected_gain = center + scale * reference_predictions[i]
        for t, key in enumerate(TARGETS):
            expected = rows[i]["at10"][key] + max(0.0, float(expected_gain[t]))
            max_error = max(max_error, abs(portable[key] - expected))
    if max_error > 1e-7:
        raise ValueError("Portable predictor differs from the training library.")
    result["export_validation"] = {"rows": len(indexes), "max_absolute_prediction_difference": max_error,
                                   "tolerance": 1e-7}
    return result


def _tree_predict(tree, vector):
    node = 0
    while tree["left"][node] != -1:
        node = tree["left"][node] if vector[tree["feature"][node]] <= tree["threshold"][node] else tree["right"][node]
    return tree["value"][node]


def predict_bundle(bundle, row):
    """Return predicted cumulative minute-twenty values using only stdlib."""
    estimator = bundle["estimator"]
    kind = estimator["kind"]
    if kind == "legacy":
        return {key: values[0] for key, values in coach_model._forecast(estimator["fit"], row).items()}
    if kind == "rank_median":
        gain = bundle["baseline"].get(str(row["rank"]), bundle["baseline"]["all"])
    elif kind == "extrapolation":
        gain = [row["at10"][key] for key in TARGETS]
    else:
        vector = vectorize(row, bundle["preprocessing"])
        if estimator.get("float32_inputs"):
            vector = [struct.unpack("f", struct.pack("f", value))[0] for value in vector]
        if kind == "linear":
            raw = [b + sum(c * v for c, v in zip(coef, vector))
                   for b, coef in zip(estimator["intercept"], estimator["coefficients"])]
        elif kind == "forest":
            leaf_values = [_tree_predict(tree, vector) for tree in estimator["trees"]]
            raw = [mean(values[i] for values in leaf_values) for i in range(len(TARGETS))]
        elif kind == "boosting":
            raw = [output["initial"] + output["learning_rate"] *
                   sum(_tree_predict(tree, vector)[0] for tree in output["trees"])
                   for output in estimator["outputs"]]
        else:
            raise ValueError("Unsupported portable estimator.")
        scaling = bundle["target_scaling"]
        gain = [c + s * v for c, s, v in zip(scaling["center"], scaling["scale"], raw)]
    return {key: row["at10"][key] + max(0.0, gain[i]) for i, key in enumerate(TARGETS)}


def _records(bundle, games):
    records = []
    for game in games:
        for index, row in enumerate(game["rows"]):
            predicted = predict_bundle(bundle, row)
            records.append({"match_id": game["match_id"], "player_index": index, "hero": row["hero"],
                            "rank": row["rank"], "reported_position": row.get("reported_position"),
                            "patch": game.get("patch"), "rank_tier": row.get("rank_tier", game.get("rank_tier")),
                            "at10": row["at10"], "actual": row["at20"],
                            "predicted": predicted,
                            "errors": {key: predicted[key] - row["at20"][key] for key in TARGETS}})
    return records


def _metrics(records):
    return {key: {"mae": mean(abs(r["errors"][key]) for r in records) if records else None,
                  "rmse": math.sqrt(mean(r["errors"][key] ** 2 for r in records)) if records else None,
                  "bias": mean(r["errors"][key] for r in records) if records else None,
                  "n": len(records)} for key in TARGETS}


def select_model(development_games, protocol=None, progress=None):
    """Select solely on development rolling validation; never accepts a holdout."""
    games = _games(development_games)
    protocol = deepcopy(protocol) if protocol is not None else make_protocol(games)
    expected_hash = protocol.pop("sha256", None)
    if canonical_hash(protocol) != expected_hash:
        raise ValueError("Protocol changed after its preregistration hash was recorded.")
    protocol["sha256"] = expected_hash
    if protocol["development_sha256"] != canonical_hash(games):
        raise ValueError("Development data differ from the frozen protocol.")
    by_id = {g["match_id"]: g for g in games}
    all_records = {recipe["name"]: [] for recipe in protocol["recipes"]}
    fold_reports = []
    for number, fold in enumerate(protocol["folds"], 1):
        train = [by_id[mid] for mid in fold["train_match_ids"]]
        valid = [by_id[mid] for mid in fold["validation_match_ids"]]
        report = {"fold": number, "train_games": len(train), "validation_games": len(valid), "candidates": {}}
        for recipe in protocol["recipes"]:
            if progress is not None:
                progress({"stage": "candidate_started", "fold": number, "candidate": recipe["name"]})
            fitted = fit_candidate(recipe, train, protocol["feature_names"])
            records = _records(fitted, valid)
            all_records[recipe["name"]].extend(records)
            report["candidates"][recipe["name"]] = _metrics(records)
            if progress is not None:
                progress({"stage": "candidate_finished", "fold": number, "candidate": recipe["name"],
                          "metrics": report["candidates"][recipe["name"]]})
        fold_reports.append(report)
    reference = _metrics(all_records["rank_median"])
    leaderboard = []
    for recipe in protocol["recipes"]:
        metrics = _metrics(all_records[recipe["name"]])
        score = mean(metrics[key]["mae"] / max(reference[key]["mae"], 1e-12) for key in TARGETS)
        leaderboard.append({"name": recipe["name"], "score": score, "metrics": metrics})
    winner = leaderboard[0]
    for candidate in leaderboard[1:]:
        if candidate["score"] < winner["score"] - 1e-12:
            winner = candidate
    winning_records = all_records[winner["name"]]
    uncertainty = {"method": "rolling_out_of_fold_absolute_residual_p90", "nominal_coverage": 0.9,
                   "absolute_error_p90": {key: _percentile([abs(row["errors"][key]) for row in winning_records], 0.9)
                                          for key in TARGETS},
                   "players": len(winning_records), "games": len({row["match_id"] for row in winning_records}),
                   "limitation": "Descriptive bands from the same development CV used for selection; not an independent conformal calibration or coverage guarantee."}
    return {"format": "economy-model-selection-1", "protocol": protocol, "folds": fold_reports,
            "leaderboard": leaderboard, "winner": winner["name"],
            "uncertainty": uncertainty,
            "winner_recipe": deepcopy(next(r for r in protocol["recipes"] if r["name"] == winner["name"])),
            "selection_scope": "original development matches only; no external evaluations used"}


def _percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def paired_bootstrap(records, reference_records, repetitions=2000, seed=SEED):
    """Paired 95% percentile CI; whole matches, not correlated player rows."""
    if repetitions < 1:
        raise ValueError("Bootstrap repetitions must be positive.")
    reference = {(r["match_id"], r["player_index"]): r for r in reference_records}
    if len(reference) != len(reference_records) or set(reference) != {(r["match_id"], r["player_index"]) for r in records}:
        raise ValueError("Paired evaluation requires the same unique players in both predictors.")
    groups = defaultdict(list)
    for row in records:
        other = reference[(row["match_id"], row["player_index"])]
        groups[row["match_id"]].append([abs(row["errors"][key]) - abs(other["errors"][key]) for key in TARGETS])
    if any(len(group) != 10 for group in groups.values()):
        raise ValueError("Bootstrap units must contain all ten players of each match.")
    differences = [[mean(column) for column in zip(*group)] for _, group in sorted(groups.items())]
    result = {"unit": "whole match", "games": len(differences), "repetitions": repetitions,
              "confidence": 0.95, "seed": seed, "interpretation": "Negative delta means lower MAE than the reference."}
    samples = [[] for _ in TARGETS]
    if len(differences) > 1:
        rng = random.Random(seed)
        for _ in range(repetitions):
            selected = [differences[rng.randrange(len(differences))] for _ in differences]
            for i in range(len(TARGETS)):
                samples[i].append(mean(row[i] for row in selected))
    result["targets"] = {key: {"mae_delta": mean(row[i] for row in differences) if differences else None,
                               "ci95": [_percentile(samples[i], 0.025), _percentile(samples[i], 0.975)] if samples[i] else None}
                         for i, key in enumerate(TARGETS)}
    return result


def error_analysis(bundle, records):
    groups = defaultdict(list)
    low, high = bundle["farm_bin_edges"]
    for row in records:
        support = bundle["hero_counts"].get(str(row["hero"]), 0)
        cohort = "unseen_0" if support == 0 else "rare_1_19" if support < 20 else "seen_20_plus"
        farm = "low" if row["at10"]["lh"] < low else "middle" if row["at10"]["lh"] < high else "high"
        for name in ("farm_rank_10/" + str(row["rank"]), "hero_train_support/" + cohort,
                     "lh_at10_train_tertiles/" + farm, "patch/" + str(row["patch"])):
            groups[name].append(row)
        if row["reported_position"] in range(1, 6):
            groups["retrospective_position_audit/" + str(row["reported_position"])].append(row)
        if row.get("rank_tier") is not None:
            groups["rank_tier_audit/" + str(row["rank_tier"])].append(row)
    return {"slice_warning": "Descriptive correlated slices; small groups do not establish an improvement.",
            "farm_edges_fit_on_train": [low, high],
            "slices": {name: {"players": len(rows), "games": len({r["match_id"] for r in rows}),
                               "small_sample": len({r["match_id"] for r in rows}) < 20,
                               "metrics": _metrics(rows)} for name, rows in sorted(groups.items())},
            "largest_absolute_errors": {key: [{"match_id": row["match_id"], "player_index": row["player_index"],
                                                  "hero": row["hero"], "farm_rank_10": row["rank"],
                                                  "at10": row["at10"][key], "actual": row["actual"][key],
                                                  "predicted": row["predicted"][key],
                                                  "absolute_error": abs(row["errors"][key]),
                                                  "match_url": "https://www.opendota.com/matches/" + str(row["match_id"])}
                                                 for row in sorted(records, key=lambda r: abs(r["errors"][key]), reverse=True)[:10]]
                                        for key in TARGETS}}


def _interval_coverage(bundle, records):
    uncertainty = bundle.get("uncertainty")
    if not uncertainty:
        return None
    result = {"method": uncertainty["method"], "nominal_coverage": uncertainty["nominal_coverage"],
              "limitation": uncertainty["limitation"], "targets": {}}
    for key in TARGETS:
        radius = uncertainty["absolute_error_p90"][key]
        intervals = [(max(row["at10"][key], row["predicted"][key] - radius), row["predicted"][key] + radius,
                      row["actual"][key]) for row in records]
        result["targets"][key] = {"players": len(records), "radius": radius,
                                  "empirical_coverage": mean(low <= actual <= high for low, high, actual in intervals) if intervals else None,
                                  "mean_width": mean(high - low for low, high, _ in intervals) if intervals else None}
    return result


def evaluate_candidates(bundles, named_game_sets, reference="ridge_legacy", repetitions=2000):
    """Descriptive external evaluation. This function never selects or refits."""
    if reference not in bundles:
        raise ValueError("The fixed reference predictor is missing.")
    output = {}
    for name, source in named_game_sets.items():
        games = _games(source)
        for bundle in bundles.values():
            if set(bundle["train_match_ids"]) & {g["match_id"] for g in games}:
                raise ValueError("Evaluation contains a training match.")
            if games and min(g["start"] for g in games) < bundle["train_last_end"]:
                raise ValueError("External evaluations must start after all training matches finished.")
        records = {candidate: _records(bundle, games) for candidate, bundle in bundles.items()}
        output[name] = {"games": len(games), "players": len(games) * 10,
                        "match_ids": [g["match_id"] for g in games],
                        "first_start": games[0]["start"] if games else None,
                        "last_end": max((g["end"] for g in games), default=None),
                        "candidates": {candidate: {"metrics": _metrics(rows),
                                                    "uncertainty": _interval_coverage(bundles[candidate], rows),
                                                    "paired_against_" + reference: paired_bootstrap(rows, records[reference], repetitions),
                                                    "error_analysis": error_analysis(bundles[candidate], rows)}
                                       for candidate, rows in records.items()}}
    return output
