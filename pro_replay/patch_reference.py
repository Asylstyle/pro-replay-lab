"""Patch-specific economic benchmarks from verified OpenDota matches."""

from collections import Counter, defaultdict
from functools import lru_cache
import json
from pathlib import Path
from statistics import mean
import time

from .coach_features import game_features, row_features
from .coach_model import TARGETS
from .ml_experiments import (_metrics, _records, canonical_hash, chronological_folds,
                             fit_candidate, predict_bundle)
from .reference_policy import reference_eligibility, resolve_patch
from .source import SourceError, valve_patches


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
COHORTS = ("professional", "ranked_7300")
RECIPES = ("rank_median", "ridge_legacy", "ridge_rich_1", "ridge_rich_10", "ridge_rich_100")


def _select_games(matches, policy):
    games = {}
    excluded = Counter()
    rejected_ids = set()
    for raw in matches:
        cohort, reason = reference_eligibility(raw, policy)
        if not cohort:
            excluded[reason] += 1
            continue
        game, reason = game_features(raw, ranked=cohort == "ranked_7300")
        if reason:
            excluded[reason] += 1
            continue
        game["cohort"] = cohort
        mid = game["match_id"]
        if mid in rejected_ids:
            continue
        if mid in games:
            if game != games[mid]:
                del games[mid]
                rejected_ids.add(mid)
                excluded["conflicting_match"] += 1
            else:
                excluded["duplicate_match"] += 1
            continue
        games[mid] = game
    return sorted(games.values(), key=lambda g: (g["start"], g["match_id"])), dict(excluded)


def _benchmarks(games, group_field):
    groups = defaultdict(list)
    for game in games:
        for row in game["rows"]:
            if row.get(group_field) in range(1, 6):
                groups[(game["cohort"], str(row[group_field]))].append(row)
    result = {name: {} for name in COHORTS}
    for (source, rank), rows in groups.items():
        result[source][rank] = {"players": len(rows), **{
            moment: {k: mean(row[moment][k] for row in rows) for k in TARGETS}
            for moment in ("at10", "at20")}}
    return result


def train_reference(matches, policy):
    """Choose a recipe using earlier games. Evaluate once before the all-data refit."""
    games, excluded = _select_games(matches, policy)
    if not games:
        raise ValueError(f"Подходящих матчей патча пока нет. Исключения: {excluded}")
    counts = dict.fromkeys(COHORTS, 0)
    counts.update(Counter(g["cohort"] for g in games))
    reference = {"format": "patch-economic-reference-1", "policy": policy, "counts": counts,
                 "excluded": excluded, "means": _benchmarks(games, "rank"),
                 "position_means": _benchmarks(games, "reported_position"),
                 "source_data_hash": canonical_hash(games), "cohort_ids": {
                     s: [g["match_id"] for g in games if g["cohort"] == s] for s in COHORTS}}
    cutoff = games[len(games) * 4 // 5]["start"]
    train = [g for g in games if g["end"] <= cutoff and g["start"] < cutoff]
    test = [g for g in games if g["start"] >= cutoff]
    # On a new patch, observed means are available before there are enough
    # consecutive matches for leakage-free selection and a later evaluation.
    try:
        folds = chronological_folds(train)
    except ValueError:
        return {**reference, "fit": None, "validation": {"winner": None, "status": "insufficient_time_windows"}}
    try:
        from threadpoolctl import threadpool_limits
    except ImportError as exc:
        raise ValueError("Для обучения установи зависимости из requirements-ml.txt.") from exc
    with threadpool_limits(limits=1):
        validation = {}
        for name in RECIPES:
            records = []
            for earlier, later in folds:
                records.extend(_records(fit_candidate(name, earlier), later))
            validation[name] = _metrics(records)
        def score(name):
            return mean(validation[name][key]["mae"] / max(validation["rank_median"][key]["mae"], 1e-9)
                        for key in TARGETS)
        winner = min(RECIPES, key=score)
        candidates = {name: fit_candidate(name, train) for name in dict.fromkeys(("rank_median", "ridge_legacy", winner))}
        metrics = {}
        for source in ("all", *COHORTS):
            subset = test if source == "all" else [g for g in test if g["cohort"] == source]
            metrics[source] = {name: _metrics(_records(fit, subset)) for name, fit in candidates.items()}
        fit = fit_candidate(winner, games)
    return {**reference, "fit": fit,
            "validation": {"winner": winner, "candidate_cv": validation, "holdout": metrics,
                           "selection_train_ids": [g["match_id"] for g in train],
                           "holdout_ids": [g["match_id"] for g in test],
                           "folds": [{"train_ids": [g["match_id"] for g in a], "validation_ids": [g["match_id"] for g in b]}
                                     for a, b in folds],
                           "cutoff": cutoff, "purged": len(games)-len(train)-len(test),
                           "production_refit": "all qualifying matches after evaluation"}}


def load_reference(path=None):
    local = Path("data/patch-reference.json")
    selected = Path(path) if path else local if local.is_file() else EXAMPLES / "patch-reference-model.json"
    model = json.loads(selected.read_text(encoding="utf8"))
    if model.get("format") != "patch-economic-reference-1":
        raise ValueError("Не удалось прочитать модель ориентиров патча.")
    return model


@lru_cache(maxsize=1)
def _verified_calendar(hour):
    return valve_patches(), int(time.time())


def reference_for_player(model, raw, slot, position=None):
    policy = model["policy"]
    start = raw.get("start_time")
    if type(start) is not int or start < policy["patch_start"]:
        return {"reason": f"Для этой карты нет ориентира патча {policy['patch']}."}
    if start > policy["as_of"]:
        try:
            calendar, checked = _verified_calendar(int(time.time()) // 3600)
            if start > checked:
                # A cached calendar predating the match cannot establish its patch.
                calendar, checked = valve_patches(), int(time.time())
            if start > checked or resolve_patch(calendar, start) != policy["patch"]:
                return {"reason": "Для патча этой игры ещё нет обученного ориентира."}
        except (SourceError, ValueError):
            return {"reason": "Не удалось сверить патч этой игры с календарём Valve."}
    row = row_features(raw).get(slot)
    if not row or row.get("rank") is None or any(k not in row["at10"] for k in TARGETS):
        return {"reason": "Для ориентира нужны показатели игрока и золото пяти союзников на 10:00."}
    expected = predict_bundle(model["fit"], row) if model["fit"] else {}
    validation = model["validation"]
    errors = validation["holdout"]["all"][validation["winner"]] if expected else {}
    metrics = []
    for key in TARGETS:
        actual = row["at20"].get(key)
        if actual is not None and actual < row["at10"][key]:
            actual = None
        metrics.append({"key": key, "label": TARGETS[key][1], "actual": actual, "expected": expected.get(key),
                        "mae": errors.get(key, {}).get("mae"),
                        "gap": actual - expected[key] if actual is not None and expected else None})
    groups = model["position_means"] if position in range(1, 6) else model["means"]
    group_key = position if position in range(1, 6) else row["rank"]
    return {"patch": policy["patch"], "rank": row["rank"], "position": position, "counts": model["counts"],
            "in_reference": any(raw.get("match_id") in ids for ids in model["cohort_ids"].values()),
            "groups": {s: groups[s].get(str(group_key)) for s in COHORTS}, "metrics": metrics,
            "at10": row["at10"]}
