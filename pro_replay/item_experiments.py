"""Temporal model comparison for the recorded next-item task.

Training requires the optional ML dependencies. Exported models and their
predictions use only the standard library. A purchase prediction describes
observed player behaviour; it does not establish which item would win a fight.
"""

import hashlib
import json
import math
import random
from collections import Counter, defaultdict

from . import learning
from .analysis import CORE_ITEMS


RECIPES = (
    {"name": "global_prior", "kind": "prior", "alpha": 1.0},
    {"name": "hero_prior_1", "kind": "hero_prior", "alpha": 1.0},
    {"name": "hero_prior_5", "kind": "hero_prior", "alpha": 5.0},
    {"name": "legacy_softmax", "kind": "softmax"},
    {"name": "logistic_c01", "kind": "logistic", "C": 0.1},
    {"name": "logistic_c1", "kind": "logistic", "C": 1.0},
    {"name": "logistic_history_c1", "kind": "logistic", "C": 1.0, "history": True},
    {"name": "small_forest", "kind": "forest", "trees": 80, "depth": 6, "leaf": 3},
    {"name": "legacy_hero_frequency", "kind": "hero_frequency"},
)
PROTOCOL = {
    "version": 1,
    "target": learning.TARGET,
    "population": "one player; only matches with an observed target purchase",
    "features": "hero, patch, LH/gold/XP at exactly 10:00; history variant adds purchases at time<=600",
    "split": "last 20% by match start time is the legacy test; same timestamps stay together",
    "validation": "expanding windows: first 50% -> next 25%, first 75% -> last 25% of development",
    "selection": "highest pooled validation accuracy, then lowest log-loss, then recipe name",
    "test_status": "previously reported benchmark, not a newly sealed holdout",
    "seed": 41,
    "threads": 1,
    "unseen_label_probability_floor": 1e-12,
    "confidence_threshold": 0.6,
    "recipes": list(RECIPES),
}


def prepare_rows(rows):
    """Validate identity/dates and reject missing labels without imputing them."""
    valid, seen, skipped, accounts = [], set(), Counter(), set()
    total = 0
    for row in rows:
        total += 1
        if row.get("is_demo") or row.get("synthetic"):
            skipped["demo_row"] += 1
            continue
        if (type(row.get("match_id")) is not int
                or not learning._number(row.get("start_time")) or row["start_time"] <= 0):
            skipped["missing_match_or_date"] += 1
            continue
        if not learning._has_snapshot(row):
            skipped["missing_10_minute_snapshot"] += 1
            continue
        label, reason = learning._label(row)
        if reason:
            skipped[reason] += 1
            continue
        if row.get("account_id") is not None:
            accounts.add(row["account_id"])
        if row["match_id"] in seen:
            skipped["duplicate_match"] += 1
            continue
        seen.add(row["match_id"])
        valid.append((row, label))
    if len(accounts) > 1:
        raise ValueError("The next-item experiment is per-player; do not mix accounts.")
    valid.sort(key=lambda pair: (pair[0]["start_time"], pair[0]["match_id"]))
    return valid, {"input_rows": total, "eligible_matches": len(valid), "excluded": dict(skipped)}


def temporal_split(samples, fraction):
    """Keep the complete timestamp block on the newer side of a split."""
    if not samples or not 0 < fraction < 1:
        raise ValueError("A nonempty dataset and fraction in (0, 1) are required.")
    cutoff = samples[min(len(samples) - 1, int(len(samples) * fraction))][0]["start_time"]
    return ([pair for pair in samples if pair[0]["start_time"] < cutoff],
            [pair for pair in samples if pair[0]["start_time"] >= cutoff])


def validation_folds(development):
    earlier, later = temporal_split(development, 0.75)
    first, middle = temporal_split(development, 0.5)
    middle = [pair for pair in middle if pair[0]["start_time"] < later[0][0]["start_time"]]
    folds = [(first, middle), (earlier, later)]
    if any(len(train) < 10 or not valid for train, valid in folds):
        raise ValueError("Insufficient distinct dates for two expanding validation folds.")
    return folds


def _history(row):
    # The target log is never itself a feature: this function filters every event
    # at the prediction boundary, including when a complete match row is passed.
    counts = Counter()
    for event in row.get("core_items", []):
        if (isinstance(event, dict) and learning._number(event.get("time"))
                and event["time"] <= 600 and event.get("key") in CORE_ITEMS):
            counts[event["key"]] += 1
    return counts


def _vector(row, prep):
    result = [0.0] * prep["size"]
    for index, value in learning._features(row, prep):
        result[index] = value
    counts = _history(row) if prep.get("history") else {}
    for key, index in prep.get("history", {}).items():
        result[index] = min(counts.get(key, 0), 3)
    return result


def _preprocess(train, history=False):
    prep = learning._preprocess(train)
    if history:
        keys = sorted({key for row, _ in train for key in _history(row)})
        prep["history"] = {key: prep["size"] + i for i, key in enumerate(keys)}
        prep["size"] += len(keys)
    return prep


def fit_item_model(train, recipe):
    """Fit one frozen recipe and export a JSON-only model, without any test rows."""
    if not train:
        raise ValueError("Training data is empty.")
    classes = sorted({label for _, label in train})
    counts = Counter(label for _, label in train)
    model = {"format": 1, "task": "recorded_next_item", "target": learning.TARGET,
             "recipe": dict(recipe), "classes": classes,
             "training_hero_counts": dict(Counter(str(row.get("hero_id")) for row, _ in train))}
    kind = recipe["kind"]
    if kind in ("prior", "hero_prior", "hero_frequency") or len(classes) == 1:
        model["kind"] = kind if kind in ("prior", "hero_prior", "hero_frequency") else "prior"
        model["prior"] = [(counts[label] + 1) / (len(train) + len(classes)) for label in classes]
        per_hero = defaultdict(Counter)
        for row, label in train:
            per_hero[str(row.get("hero_id"))][label] += 1
        model["hero_counts"] = {hero: [values[label] for label in classes]
                                for hero, values in per_hero.items()}
        return model
    if kind == "softmax":
        model["kind"] = "softmax"
        model["legacy"] = learning._fit(train)
        return model
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    model["kind"] = kind
    model["preprocessing"] = prep = _preprocess(train, recipe.get("history", False))
    features = [_vector(row, prep) for row, _ in train]
    labels = [label for _, label in train]
    if kind == "logistic":
        estimator = LogisticRegression(C=recipe["C"], max_iter=500, random_state=41)
    elif kind == "forest":
        estimator = RandomForestClassifier(n_estimators=recipe["trees"], max_depth=recipe["depth"],
                                           min_samples_leaf=recipe["leaf"], random_state=41, n_jobs=1)
    else:
        raise ValueError(f"Unknown item model kind: {kind}")
    estimator.fit(features, labels)
    model["classes"] = estimator.classes_.tolist()
    if kind == "logistic":
        model["weights"] = estimator.coef_.tolist()
        model["intercepts"] = estimator.intercept_.tolist()
        model["iterations"] = estimator.n_iter_.tolist()
    else:
        model["trees"] = [
            {"left": tree.tree_.children_left.tolist(), "right": tree.tree_.children_right.tolist(),
             "feature": tree.tree_.feature.tolist(), "threshold": tree.tree_.threshold.tolist(),
             "values": tree.tree_.value[:, 0, :].tolist()}
            for tree in estimator.estimators_
        ]
    return model


def item_probabilities(model, row):
    """Return all class probabilities without loading sklearn or pickle files."""
    if not learning._has_snapshot(row):
        return None
    kind = model["kind"]
    if kind in ("prior", "hero_prior", "hero_frequency"):
        probabilities = model["prior"]
        if kind in ("hero_prior", "hero_frequency"):
            counts = model["hero_counts"].get(str(row.get("hero_id")))
            if counts:
                alpha = model["recipe"].get("alpha", 0.0)
                probabilities = [(count + alpha * prior) / (sum(counts) + alpha)
                                 for count, prior in zip(counts, probabilities)]
    elif kind == "softmax":
        legacy = model["legacy"]
        probabilities = learning._probabilities(legacy["weights"], learning._features(row, legacy["preprocessing"]))
    elif kind == "logistic":
        values = _vector(row, model["preprocessing"])
        logits = [bias + sum(weight * value for weight, value in zip(weights, values))
                  for weights, bias in zip(model["weights"], model["intercepts"])]
        if len(model["classes"]) == 2:
            logits = [0.0, logits[0]]
        maximum = max(logits)
        exps = [math.exp(value - maximum) for value in logits]
        probabilities = [value / sum(exps) for value in exps]
    elif kind == "forest":
        values = _vector(row, model["preprocessing"])
        probabilities = [0.0] * len(model["classes"])
        for tree in model["trees"]:
            node = 0
            while tree["left"][node] != -1:
                node = (tree["left"][node] if values[tree["feature"][node]] <= tree["threshold"][node]
                        else tree["right"][node])
            leaf = tree["values"][node]
            total = sum(leaf)
            for i, value in enumerate(leaf):
                probabilities[i] += value / total / len(model["trees"])
    else:
        raise ValueError(f"Unknown item model kind: {kind}")
    return dict(zip(model["classes"], probabilities))


def predict_item(model, row):
    probabilities = item_probabilities(model, row)
    if probabilities is None:
        return None
    ranked = sorted(probabilities.items(), key=lambda pair: (-pair[1], pair[0]))
    support = model.get("training_hero_counts", {}).get(str(row.get("hero_id")), 0)
    return {"item": ranked[0][0], "probability": ranked[0][1],
            "top_items": [{"item": item, "probability": probability} for item, probability in ranked[:3]],
            "probability_is_calibrated": False, "training_hero_examples": support,
            "unseen_hero": support == 0,
            "warning": ("This probability is an uncalibrated model score, not a reliable confidence or chance of winning."
                        + (" No training matches for this hero." if not support else
                           f" Only {support} training matches for this hero." if support < 5 else "")),
            "interpretation": "observed purchase prediction, not an optimal-build recommendation"}


def _predictions(model, samples):
    predictions = []
    for row, label in samples:
        probs = item_probabilities(model, row)
        ranked = sorted(probs, key=lambda key: (-probs[key], key))
        predictions.append({"match_id": row["match_id"], "start_time": row["start_time"],
                            "hero_id": row.get("hero_id"), "hero_name": row.get("hero_name"),
                            "training_hero_examples": model["training_hero_counts"].get(str(row.get("hero_id")), 0),
                            "unseen_hero": str(row.get("hero_id")) not in model["training_hero_counts"],
                            "actual": label, "predicted": ranked[0], "confidence": probs[ranked[0]],
                            "top3": ranked[:3], "actual_probability": probs.get(label, 0.0),
                            "unseen_label": label not in probs,
                            "brier": sum((prob - (key == label)) ** 2 for key, prob in probs.items())
                                     + int(label not in probs)})
    return predictions


def classification_metrics(predictions):
    if not predictions:
        raise ValueError("Cannot score an empty prediction set.")
    count = len(predictions)
    labels = sorted({p[key] for p in predictions for key in ("actual", "predicted")})
    per_label = []
    for label in labels:
        tp = sum(p["actual"] == label and p["predicted"] == label for p in predictions)
        actual = sum(p["actual"] == label for p in predictions)
        predicted = sum(p["predicted"] == label for p in predictions)
        per_label.append({"item": label, "support": actual, "predicted": predicted,
                          "f1": 2 * tp / (actual + predicted) if actual + predicted else 0.0})
    correct = sum(p["actual"] == p["predicted"] for p in predictions)
    accuracy = correct / count
    # Wilson interval: uncertainty over matches, not a guarantee after model selection.
    z = 1.959963984540054
    center = (accuracy + z*z/(2*count)) / (1 + z*z/count)
    half = z * math.sqrt(accuracy*(1-accuracy)/count + z*z/(4*count*count)) / (1+z*z/count)
    bins = []
    for index in range(5):
        group = [p for p in predictions if min(int(p["confidence"] * 5), 4) == index]
        if group:
            bins.append({"lower": index / 5, "upper": (index + 1) / 5, "count": len(group),
                         "confidence": sum(p["confidence"] for p in group) / len(group),
                         "accuracy": sum(p["actual"] == p["predicted"] for p in group) / len(group)})
    confident = [p for p in predictions if p["confidence"] >= PROTOCOL["confidence_threshold"]]
    return {"matches": count, "correct": correct, "accuracy": accuracy,
            "accuracy_wilson_95": [center-half, center+half],
            "macro_f1": sum(p["f1"] for p in per_label) / len(per_label),
            "top3_accuracy": sum(p["actual"] in p["top3"] for p in predictions) / count,
            "log_loss": -sum(math.log(max(p["actual_probability"], 1e-12)) for p in predictions) / count,
            "brier": sum(p["brier"] for p in predictions) / count,
            "ece_5_bins": sum(p["count"] * abs(p["confidence"]-p["accuracy"]) for p in bins) / count,
            "unseen_labels": sum(p["unseen_label"] for p in predictions),
            "unseen_hero_matches": sum(p.get("unseen_hero", False) for p in predictions),
            "unseen_hero_errors": sum(p.get("unseen_hero", False) and p["actual"] != p["predicted"]
                                      for p in predictions),
            "confidence_bins": bins, "per_item": per_label,
            "confidence_threshold": PROTOCOL["confidence_threshold"],
            "confident_coverage": len(confident) / count,
            "confident_accuracy": (sum(p["actual"] == p["predicted"] for p in confident) / len(confident)
                                   if confident else None)}


def error_analysis(predictions):
    groups = defaultdict(list)
    confusion = Counter()
    errors = []
    for prediction in predictions:
        groups[str(prediction["hero_id"])].append(prediction)
        if prediction["actual"] != prediction["predicted"]:
            confusion[(prediction["actual"], prediction["predicted"])] += 1
            errors.append({**prediction, "match_url": f"https://www.opendota.com/matches/{prediction['match_id']}"})
    return {"by_hero": [
                {"hero_id": hero, "hero_name": group[0].get("hero_name"), "matches": len(group),
                 "errors": sum(p["actual"] != p["predicted"] for p in group),
                 "accuracy": sum(p["actual"] == p["predicted"] for p in group) / len(group)}
                for hero, group in sorted(groups.items())],
            "confusions": [{"actual": pair[0], "predicted": pair[1], "count": count}
                           for pair, count in confusion.most_common()],
            "errors": sorted(errors, key=lambda p: (-p["confidence"], p["match_id"]))}


def paired_accuracy_difference(selected, reference):
    """Paired match bootstrap; descriptive, not an independent significance claim."""
    other = {prediction["match_id"]: prediction for prediction in reference}
    if len(other) != len(reference) or {p["match_id"] for p in selected} != set(other):
        raise ValueError("Paired comparison requires the same distinct matches.")
    if any(p["actual"] != other[p["match_id"]]["actual"] for p in selected):
        raise ValueError("Paired comparison requires identical target labels.")
    differences = [int(p["predicted"] == p["actual"])
                   - int(other[p["match_id"]]["predicted"] == p["actual"]) for p in selected]
    if not differences:
        raise ValueError("Paired comparison is empty.")
    rng = random.Random(41)
    draws = sorted(sum(rng.choice(differences) for _ in differences) / len(differences) for _ in range(2000))
    return {"accuracy_delta": sum(differences) / len(differences),
            "paired_bootstrap_95": [draws[49], draws[1949]], "resamples": 2000,
            "selected_only_correct": differences.count(1), "reference_only_correct": differences.count(-1)}


def run_item_experiment(rows, recipes=None):
    """Select using earlier folds; only then score the final legacy benchmark."""
    recipes = tuple(recipes or RECIPES)
    samples, coverage = prepare_rows(rows)
    if len(samples) < 40:
        raise ValueError("At least 40 eligible distinct matches are required for comparison.")
    development, test = temporal_split(samples, 0.8)
    folds = validation_folds(development)
    comparison = []
    for recipe in recipes:
        pooled, fold_metrics = [], []
        for train, valid in folds:
            predictions = _predictions(fit_item_model(train, recipe), valid)
            pooled.extend(predictions)
            fold_metrics.append(classification_metrics(predictions))
        comparison.append({"recipe": dict(recipe), "validation": classification_metrics(pooled),
                           "folds": fold_metrics})
    selected = min(comparison, key=lambda row: (-row["validation"]["accuracy"],
                                               row["validation"]["log_loss"], row["recipe"]["name"]))
    # Selection is complete before any final test targets are used in scoring.
    winner = selected["recipe"]["name"]
    selected_model, selected_predictions = None, None
    test_predictions = {}
    for entry in comparison:
        model = fit_item_model(development, entry["recipe"])
        predictions = _predictions(model, test)
        test_predictions[entry["recipe"]["name"]] = predictions
        entry["legacy_test"] = classification_metrics(predictions)
        if entry["recipe"]["name"] == winner:
            selected_model, selected_predictions = model, predictions
    canonical = json.dumps([[{k: row.get(k) for k in ("match_id", "start_time", "hero_id", "patch",
                           "patch_version", "lh_10", "gold_10", "xp_10", "core_items")}, label]
                            for row, label in samples], sort_keys=True, separators=(",", ":"))
    protocol = {**PROTOCOL, "recipes": list(recipes)}
    selected_model["training_match_ids"] = [row["match_id"] for row, _ in development]
    selected_model["selected_using"] = "earlier expanding-window validation only"
    report = {"format": 1, "protocol": protocol,
              "protocol_sha256": hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest(),
              "dataset_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
              "coverage": coverage, "selected_recipe": winner,
              "data_profile": {"players": sorted({row.get("account_id") for row, _ in samples
                                                    if row.get("account_id") is not None}),
                               "heroes": len({row.get("hero_id") for row, _ in samples}),
                               "patch_values": sorted({str(row.get("patch")) for row, _ in samples}),
                               "patch_versions": sorted({str(row.get("patch_version")) for row, _ in samples}),
                               "train_labels": dict(Counter(label for _, label in development)),
                               "test_labels": dict(Counter(label for _, label in test))},
              "split": {"train_match_ids": [row["match_id"] for row, _ in development],
                        "legacy_test_match_ids": [row["match_id"] for row, _ in test],
                        "folds": [{"train_match_ids": [row["match_id"] for row, _ in train],
                                   "validation_match_ids": [row["match_id"] for row, _ in valid]}
                                  for train, valid in folds]},
              "comparison": comparison, "selected_legacy_test_predictions": selected_predictions,
              "selected_vs_references": {name: paired_accuracy_difference(selected_predictions, predictions)
                                         for name, predictions in test_predictions.items()
                                         if name in ("legacy_softmax", "hero_prior_1", "legacy_hero_frequency")},
              "error_analysis": error_analysis(selected_predictions),
              "limitations": ["The final 20% was reported before this experiment: it is not a sealed holdout.",
                              "A 100-match, single-player sample cannot establish performance for all five roles.",
                              "Macro F1 covers the union of observed and predicted labels in each scoring set.",
                              "Probability calibration estimates are descriptive on this small sample.",
                              "Prediction imitates a recorded purchase; no causal optimal-item label is available.",
                              "Matches without a target purchase in the window are excluded; coverage is conditional."]}
    return {"report": report, "model": selected_model}
