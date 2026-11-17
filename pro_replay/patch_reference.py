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
FORMAT = "patch-economic-reference-2"
LEGACY_FORMAT = "patch-economic-reference-1"
PACKAGED_MODEL = "patch-cohort-model.json"
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
    """Fit, select and score within each population independently."""
    games, excluded = _select_games(matches, policy)
    if not games:
        raise ValueError(f"Подходящих матчей патча пока нет. Исключения: {excluded}")
    counts = dict.fromkeys(COHORTS, 0)
    counts.update(Counter(g["cohort"] for g in games))
    populations = {name: [g for g in games if g["cohort"] == name] for name in COHORTS}
    return {"format": FORMAT, "policy": policy, "counts": counts, "excluded": excluded,
            "means": _benchmarks(games, "rank"), "position_means": _benchmarks(games, "reported_position"),
            "source_data_hash": canonical_hash(games),
            "cohort_ids": {name: [g["match_id"] for g in subset] for name, subset in populations.items()},
            "cohorts": {name: _train_population(subset) for name, subset in populations.items()}}


def _train_population(games):
    no_fit = {"fit": None, "validation": {"winner": None, "status": "insufficient_time_windows", "holdout_ids": []}}
    if not games:
        return no_fit
    cutoff = games[len(games) * 4 // 5]["start"]
    train = [g for g in games if g["end"] <= cutoff and g["start"] < cutoff]
    test = [g for g in games if g["start"] >= cutoff]
    # On a new patch, observed means are available before there are enough
    # consecutive matches for leakage-free selection and a later evaluation.
    try:
        folds = chronological_folds(train)
    except ValueError:
        return no_fit
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
        metrics = {name: _metrics(_records(fit, test)) for name, fit in candidates.items()}
        fit = fit_candidate(winner, games)
    return {"fit": fit,
            "validation": {"status": "evaluated", "winner": winner, "candidate_cv": validation, "holdout": metrics,
                           "selection_train_ids": [g["match_id"] for g in train],
                           "holdout_ids": [g["match_id"] for g in test],
                           "folds": [{"train_ids": [g["match_id"] for g in a], "validation_ids": [g["match_id"] for g in b]}
                                     for a, b in folds],
                           "cutoff": cutoff, "purged": len(games)-len(train)-len(test),
                           "production_refit": "all qualifying matches in this cohort after evaluation"}}


def load_reference(path=None):
    local = Path("data/patch-reference.json")
    packaged = EXAMPLES / PACKAGED_MODEL
    selected = Path(path) if path is not None else local if local.is_file() else packaged
    model = json.loads(selected.read_text(encoding="utf8"))
    if path is None and selected == local and model.get("format") == LEGACY_FORMAT and packaged.is_file():
        current = json.loads(packaged.read_text(encoding="utf8"))
        if (current.get("format") == FORMAT
                and all(current["policy"][k] == model["policy"][k] for k in ("patch", "patch_start", "minimum_avg_mmr"))
                and current["policy"]["as_of"] >= model["policy"]["as_of"]
                and all(set(model["cohort_ids"][group]) <= set(current["cohort_ids"][group]) for group in COHORTS)):
            model = current
    if model.get("format") not in (FORMAT, LEGACY_FORMAT):
        raise ValueError("Не удалось прочитать модель ориентиров патча.")
    if model["format"] == FORMAT:
        seen = set()
        for cohort in COHORTS:
            ids = set(model["cohort_ids"][cohort])
            entry = model["cohorts"][cohort]
            if seen & ids or len(ids) != model["counts"][cohort]:
                raise ValueError("Состав групп не совпадает с зарегистрированными матчами.")
            seen.update(ids)
            if entry["fit"] is None:
                continue
            validation = entry["validation"]
            train, holdout = set(validation["selection_train_ids"]), set(validation["holdout_ids"])
            if set(entry["fit"]["train_match_ids"]) != ids or train & holdout or not (train | holdout) <= ids:
                raise ValueError("Матчи обучения или проверки не соответствуют группе модели.")
    return model


@lru_cache(maxsize=1)
def _verified_calendar(hour):
    return valve_patches(), int(time.time())


def client_cohort(raw, model=None):
    """Route a client by lobby type. The reference pubs, not the client, need verified MMR."""
    league_id, lobby = raw.get("leagueid"), raw.get("lobby_type")
    if type(league_id) is not int:
        return None
    if league_id == 0 and lobby == 7 and raw.get("game_mode") == 22:
        return "ranked_7300"
    if league_id <= 0 or lobby != 1:
        return None
    league = raw.get("league") or {}
    if not isinstance(league, dict) or league.get("leagueid") not in (None, league_id):
        return None
    tier = league.get("tier")
    if tier in ("professional", "premium"):
        return "professional"
    known = (model or {}).get("cohort_ids", {}).get("professional", ())
    if tier is None and raw.get("match_id") in known:
        return "professional"
    return None


def patch_mismatch(model, raw):
    policy = model["policy"]
    start = raw.get("start_time")
    if type(start) is not int or start < policy["patch_start"]:
        return f"Для этой карты нет ориентира патча {policy['patch']}."
    if start > policy["as_of"]:
        try:
            calendar, checked = _verified_calendar(int(time.time()) // 3600)
            if start > checked:
                # A cached calendar predating the match cannot establish its patch.
                calendar, checked = valve_patches(), int(time.time())
            if start > checked or resolve_patch(calendar, start) != policy["patch"]:
                return "Для патча этой игры ещё нет обученного ориентира."
        except (SourceError, ValueError):
            return "Не удалось сверить патч этой игры с календарём Valve."
    return None


def reference_for_player(model, raw, slot, position=None):
    source = client_cohort(raw, model)
    if source is None:
        return {"reason": "Для сравнения нужен рейтинговый All Pick или подтверждённый профессиональный матч."}
    reason = patch_mismatch(model, raw)
    if reason:
        return {"reason": reason, "cohort": source}
    count = model["counts"][source]
    context = {"cohort": source, "patch": model["policy"]["patch"]}
    if not count:
        return {**context, "reason": "На этом патче в подходящей группе пока нет матчей."}
    row = row_features(raw).get(slot)
    if not row or row.get("rank") is None or any(k not in row["at10"] for k in TARGETS):
        return {**context, "reason": "Для ориентира нужны показатели игрока и золото пяти союзников на 10:00."}
    # Older releases have audited cohort means but a single mixed-population predictor.
    population = model["cohorts"][source] if model["format"] == FORMAT else {}
    fit = population.get("fit")
    validation = population.get("validation", {"status": "legacy_means_only", "holdout_ids": []})
    expected = predict_bundle(fit, row) if fit else {}
    errors = validation["holdout"][validation["winner"]] if fit else {}
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
    return {"patch": model["policy"]["patch"], "rank": row["rank"], "position": position, "counts": {source: count},
            "cohort": source, "reference_matches": count, "validation_matches": len(validation["holdout_ids"]),
            "model_status": validation["status"], "in_reference": raw.get("match_id") in model["cohort_ids"][source],
            "groups": {source: groups[source].get(str(group_key))}, "metrics": metrics,
            "at10": row["at10"]}
