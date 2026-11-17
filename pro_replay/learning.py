"""A CPU-only, reproducible next-item classifier for one player's match history."""

import math
import random
from collections import Counter, defaultdict
from statistics import mean, pstdev


NUMERIC = ("lh_10", "gold_10", "xp_10")
CATEGORICAL = ("hero_id", "patch")
MIN_MATCHES = 30
TARGET = "First recorded tracked item purchased after 10:00 and through 30:00"


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _has_snapshot(row):
    return all(_number(row.get(name)) for name in NUMERIC)


def _label(row):
    if not row.get("purchase_log_available") or not isinstance(row.get("core_items"), list):
        return None, "missing_purchase_log"
    events = [
        (event["time"], event["key"])
        for event in row["core_items"]
        if isinstance(event, dict) and _number(event.get("time"))
        and 600 < event["time"] <= 1800 and isinstance(event.get("key"), str)
        and event["key"]
    ]
    if not events:
        return None, "no_recorded_target_in_window"
    earliest = min(time for time, _ in events)
    items = {item for time, item in events if time == earliest}
    if len(items) > 1:
        return None, "ambiguous_first_purchase"
    return items.pop(), None


def _category(row, name):
    value = row.get(name)
    return str(value) if value is not None else "<missing>"


def _preprocess(train):
    """Fit scaling and category lookup on training rows only."""
    scaling = {}
    for name in NUMERIC:
        values = [row[name] for row, _ in train]
        scaling[name] = [mean(values), pstdev(values) or 1.0]
    size = 1 + len(NUMERIC)
    categories = {}
    categories_used = list(CATEGORICAL)
    if any(row.get("patch_version") for row, _ in train):
        categories_used.append("patch_version")
    for name in categories_used:
        levels = sorted({_category(row, name) for row, _ in train})
        categories[name] = {value: i for i, value in enumerate(levels, size)}
        size += len(levels)
    return {"scaling": scaling, "categories": categories, "size": size}


def _features(row, prep):
    values = [(0, 1.0)]
    for i, name in enumerate(NUMERIC, 1):
        center, scale = prep["scaling"][name]
        values.append((i, max(-5.0, min(5.0, (row[name] - center) / scale))))
    for name in prep["categories"]:
        i = prep["categories"][name].get(_category(row, name))
        if i is not None:
            values.append((i, 1.0))
    return values


def _probabilities(weights, features):
    logits = [sum(w[i] * value for i, value in features) for w in weights]
    largest = max(logits)
    exps = [math.exp(logit - largest) for logit in logits]
    total = sum(exps)
    return [value / total for value in exps]


def _fit(train):
    prep = _preprocess(train)
    classes = sorted({label for _, label in train})
    indices = {label: i for i, label in enumerate(classes)}
    samples = [(_features(row, prep), indices[label]) for row, label in train]
    weights = [[0.0] * prep["size"] for _ in classes]
    rng = random.Random(41)
    for epoch in range(35):
        rng.shuffle(samples)
        step = 0.08 / math.sqrt(epoch + 1)
        for features, target in samples:
            probs = _probabilities(weights, features)
            for j, w in enumerate(weights):
                error = probs[j] - (j == target)
                for i, value in features:
                    w[i] -= step * error * value
    return {"format": 1, "algorithm": "softmax_sgd", "classes": classes,
            "preprocessing": prep, "weights": weights, "target": TARGET}


def predict(model, row):
    """Rank items from an at-10:00 snapshot. No later action or outcome is read."""
    if not model or not _has_snapshot(row):
        return None
    if model.get("task") == "recorded_next_item":
        from .item_experiments import predict_item
        return predict_item(model, row)
    features = _features(row, model["preprocessing"])
    probabilities = _probabilities(model["weights"], features)
    ranked = sorted(zip(model["classes"], probabilities), key=lambda pair: -pair[1])
    item, probability = ranked[0]
    return {"item": item, "probability": probability, "top_items": [
        {"item": name, "probability": prob} for name, prob in ranked[:3]
    ]}


def train_model(rows):
    """One example per match. Scores apply only where a target purchase was observed."""
    skipped = Counter()
    eligible = []
    seen = set()
    total = 0
    for row in rows:
        total += 1
        match_id = row.get("match_id")
        if row.get("synthetic") or row.get("is_demo"):
            skipped["demo_row"] += 1
            continue
        if not isinstance(match_id, int) or not _number(row.get("start_time")) or row["start_time"] <= 0:
            skipped["missing_match_or_date"] += 1
            continue
        if not _has_snapshot(row):
            skipped["missing_10_minute_snapshot"] += 1
            continue
        label, reason = _label(row)
        if reason:
            skipped[reason] += 1
            continue
        if match_id in seen:
            skipped["duplicate_match"] += 1
            continue
        seen.add(match_id)
        eligible.append((row, label))

    coverage = {"input_rows": total, "labeled_matches": len(eligible),
                "minimum_matches": MIN_MATCHES, "excluded": dict(skipped)}

    def insufficient(reason):
        return {"status": "insufficient_data", "reason": reason,
                "coverage": coverage, "metrics": None, "model": None}

    if len(eligible) < MIN_MATCHES:
        return insufficient(f"At least {MIN_MATCHES} eligible distinct matches are required.")
    eligible.sort(key=lambda pair: (pair[0]["start_time"], pair[0]["match_id"]))
    cutoff = eligible[(len(eligible) * 4) // 5][0]["start_time"]
    train = [pair for pair in eligible if pair[0]["start_time"] < cutoff]
    test = [pair for pair in eligible if pair[0]["start_time"] >= cutoff]
    counts = Counter(label for _, label in train)
    if len(train) < 20 or len(counts) < 2:
        return insufficient("Need at least 20 earlier matches and two different training labels.")
    model = _fit(train)
    majority = min(counts, key=lambda label: (-counts[label], label))
    per_hero = defaultdict(Counter)
    for row, label in train:
        per_hero[row.get("hero_id")][label] += 1
    hero_picks = {hero: min(freq, key=lambda item: (-freq[item], item)) for hero, freq in per_hero.items()}
    hits = sum(predict(model, row)["item"] == label for row, label in test)
    baseline_hits = sum(label == majority for _, label in test)
    hero_hits = sum(label == hero_picks.get(row.get("hero_id"), majority) for row, label in test)
    metrics = {
        "train_matches": len(train), "test_matches": len(test),
        "train_classes": len(counts), "test_start_time": cutoff,
        "correct": hits, "accuracy": hits / len(test),
        "baseline_item": majority, "baseline_correct": baseline_hits,
        "baseline_accuracy": baseline_hits / len(test),
        "hero_baseline_accuracy": hero_hits / len(test),
        "unseen_test_labels": sum(label not in counts for _, label in test),
    }
    return {"status": "trained", "coverage": coverage, "metrics": metrics, "model": model}
