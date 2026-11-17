"""Learn typical 10→20-minute resource gains from earlier professional games."""

from collections import Counter, defaultdict
import json
from math import sqrt
import os
from pathlib import Path
from statistics import mean, median, pstdev
import tempfile

from .analysis import _int
from .impact import SLOTS


MODEL_PATH = Path(__file__).resolve().parent.parent / "examples" / "pro-coach-model.json"
TARGETS = {"lh": ("lh_t", "Добивания", "крипов"), "gold": ("gold_t", "Полученное золото", "золота"),
           "xp": ("xp_t", "Полученный опыт", "опыта")}
RIDGE_PENALTY = 10.0
MIN_GAMES = 10


def _snapshot(player, second, duration):
    values = {}
    times = player.get("times")
    if duration is not None and duration < second:
        return values
    if not isinstance(times, list):
        return values
    exact = [i for i, t in enumerate(times) if _int(t) == second]
    if len(exact) != 1:
        return values
    index = exact[0]
    for key, (field, _, _) in TARGETS.items():
        series = player.get(field)
        if isinstance(series, list) and index < len(series):
            value = _int(series[index])
            if value is not None and value >= 0:
                values[key] = value
    return values


def _player_rows(raw):
    duration = _int(raw.get("duration"))
    players = raw.get("players")
    if not isinstance(players, list):
        return {}
    by_slot = {}
    for p in players:
        if not isinstance(p, dict):
            continue
        slot = _int(p.get("player_slot"))
        if slot not in SLOTS:
            continue
        if slot in by_slot:
            return {}
        by_slot[slot] = {"hero": _int(p.get("hero_id")), "at10": _snapshot(p, 600, duration),
                         "at20": _snapshot(p, 1200, duration), "rank": None}
    for side in (SLOTS[:5], SLOTS[5:]):
        gold = [by_slot.get(s, {}).get("at10", {}).get("gold") for s in side]
        if None not in gold:
            for slot, own in zip(side, gold):
                # Ties share a rank. Final position estimates, GPM and win/loss
                # are not available at minute 10 and never enter this estimator.
                by_slot[slot]["rank"] = 1 + sum(g > own for g in gold)
    return by_slot


def _game(raw, *, ranked=False):
    if not isinstance(raw, dict) or any(raw.get(k) for k in ("synthetic", "demo", "is_demo")):
        return None, "invalid_or_demo"
    mid, start, duration, league = (_int(raw.get(k)) for k in ("match_id", "start_time", "duration", "leagueid"))
    if not mid or mid < 0 or not start or start < 0 or not duration:
        return None, "missing_match_metadata"
    meta = raw.get("league")
    if ranked:
        if league != 0 or raw.get("lobby_type") != 7 or raw.get("game_mode") != 22:
            return None, "outside_ranked_all_pick"
    elif (not league or league < 0 or raw.get("lobby_type") != 1 or not isinstance(meta, dict)
          or meta.get("tier") not in ("professional", "premium")):
        return None, "outside_verified_pro_leagues"
    if duration < 1200:
        return None, "ended_before_minute_20"
    rows = _player_rows(raw)
    if len(rows) != 10 or any(not r["hero"] or r["hero"] < 0 or r["rank"] is None or len(r["at10"]) != len(TARGETS)
                              or len(r["at20"]) != len(TARGETS) for r in rows.values()):
        return None, "incomplete_roster_or_snapshots"
    if any(r["at20"][k] < r["at10"][k] for r in rows.values() for k in TARGETS):
        return None, "noncumulative_series"
    return {"match_id": mid, "start": start, "end": start + duration, "league_id": league,
            "rows": [rows[slot] for slot in SLOTS]}, None


def _vector(row, prep):
    values = [(0, 1.0)]
    for index, key in enumerate(TARGETS, 1):
        center, scale = prep["scaling"][key]
        values.append((index, (row["at10"][key] - center) / scale))
    if row["rank"] is not None:
        values.append((len(TARGETS) + row["rank"], 1.0))
    hero_feature = prep["heroes"].get(str(row["hero"]))
    if hero_feature is not None:
        values.append((hero_feature, 1.0))
    return values


def _solve_ridge(gram, rhs):
    """Positive-definite Cholesky solve; all three outputs share a design."""
    size = len(gram)
    lower = [[0.0] * size for _ in gram]
    for i in range(size):
        for j in range(i + 1):
            v = gram[i][j] - sum(lower[i][k] * lower[j][k] for k in range(j))
            lower[i][j] = sqrt(v) if i == j else v / lower[j][j]
    result = [[0.0] * len(TARGETS) for _ in gram]
    for t in range(len(TARGETS)):
        for i in range(size):
            result[i][t] = (rhs[i][t] - sum(lower[i][j] * result[j][t] for j in range(i))) / lower[i][i]
        for i in reversed(range(size)):
            future = sum(lower[j][i] * result[j][t] for j in range(i + 1, size))
            result[i][t] = (result[i][t] - future) / lower[i][i]
    return result


def _fit(rows):
    heroes = Counter(str(r["hero"]) for r in rows)
    first_hero_feature = 1 + len(TARGETS) + 5
    prep = {"heroes": {hero: i for i, hero in enumerate(sorted(heroes), first_hero_feature)},
            "scaling": {key: [mean(r["at10"][key] for r in rows), pstdev(r["at10"][key] for r in rows) or 1.0]
                        for key in TARGETS}}
    size = first_hero_feature + len(heroes)
    gram = [[0.0] * size for _ in range(size)]
    rhs = [[0.0] * len(TARGETS) for _ in range(size)]
    by_rank = defaultdict(list)
    for r in rows:
        x = _vector(r, prep)
        gains = [r["at20"][k] - r["at10"][k] for k in TARGETS]
        by_rank[str(r["rank"])].append(gains)
        by_rank["all"].append(gains)
        for i, xi in x:
            for j, xj in x:
                gram[i][j] += xi * xj
            for t, gain in enumerate(gains):
                rhs[i][t] += xi * gain
    for i in range(1, size):
        gram[i][i] += RIDGE_PENALTY
    baseline = {rank: [median(col) for col in zip(*group)] for rank, group in by_rank.items()}
    return {"preprocessing": prep, "weights": _solve_ridge(gram, rhs), "baseline": baseline,
            "hero_counts": dict(heroes), "rank_counts": {rank: len(v) for rank, v in by_rank.items()}}


def _forecast(fit, row):
    baseline = fit["baseline"].get(str(row["rank"]), fit["baseline"]["all"])
    vector = _vector(row, fit["preprocessing"])
    outputs = {}
    for i, key in enumerate(TARGETS):
        delta = sum(fit["weights"][j][i] * v for j, v in vector)
        outputs[key] = (row["at10"][key] + max(0, delta), row["at10"][key] + baseline[i])
    return outputs


def _prediction_errors(fit, games):
    errors = {k: [] for k in TARGETS}
    for game in games:
        for row in game["rows"]:
            for key, (prediction, baseline) in _forecast(fit, row).items():
                actual = row["at20"][key]
                errors[key].append((abs(prediction - actual), abs(baseline - actual)))
    return {key: {"mae": mean(x for x, _ in pairs) if pairs else None,
                  "baseline_mae": mean(y for _, y in pairs) if pairs else None, "n": len(pairs)}
            for key, pairs in errors.items()}


def train_coach_model(raw_matches):
    """Keep all ten players together. Fit, including scaling, on earlier games only."""
    games = {}
    excluded = Counter()
    for raw in raw_matches:
        game, reason = _game(raw)
        if reason:
            excluded[reason] += 1
        elif game["match_id"] in games:
            excluded["duplicate_match"] += 1
        else:
            games[game["match_id"]] = game
    ordered = sorted(games.values(), key=lambda g: (g["start"], g["match_id"]))
    if len(ordered) < MIN_GAMES:
        raise ValueError(f"Нужно минимум {MIN_GAMES} разных турнирных карт уровня professional/premium "
                         f"с полными снимками десяти игроков. Подошло {len(ordered)}.")
    cutoff = ordered[len(ordered) * 4 // 5]["start"]
    train = [g for g in ordered if g["end"] <= cutoff]
    test = [g for g in ordered if g["start"] >= cutoff]
    if len(train) < 5 or not test:
        raise ValueError("Недостаточно завершённых карт до хронологической границы проверки.")
    excluded["overlapping_train_test_boundary"] += len(ordered) - len(train) - len(test)
    fit = _fit([r for g in train for r in g["rows"]])
    metrics = _prediction_errors(fit, test)
    return {"format": "pro-replay-coach-1", "algorithm": "multioutput_ridge_on_10_to_20_gains",
            "penalty": RIDGE_PENALTY, "input_minute": 10, "target_minute": 20, "targets": list(TARGETS),
            "fit": fit, "metrics": metrics, "coverage": {
                "eligible_games": len(ordered), "train_games": len(train), "test_games": len(test),
                "train_players": len(train)*10, "test_players": len(test)*10, "excluded": dict(+excluded),
                "train_first_start": train[0]["start"], "train_last_end": max(g["end"] for g in train),
                "test_first_start": test[0]["start"], "test_last_end": max(g["end"] for g in test),
                "train_match_ids": [g["match_id"] for g in train], "test_match_ids": [g["match_id"] for g in test],
                "league_ids": sorted({g["league_id"] for g in ordered})}}


def evaluate_ranked(model, raw_matches):
    """Evaluate the unchanged pro fit and baseline on complete Ranked All Pick games."""
    coverage = model["coverage"]
    cutoff = coverage["train_last_end"]
    known_ids = set(coverage["train_match_ids"]) | set(coverage["test_match_ids"])
    games = {}
    excluded = Counter()
    for raw in raw_matches:
        game, reason = _game(raw, ranked=True)
        if reason:
            excluded[reason] += 1
        elif game["match_id"] in known_ids:
            excluded["overlap_with_pro_train_or_test"] += 1
        elif game["match_id"] in games:
            excluded["duplicate_ranked_match"] += 1
        else:
            games[game["match_id"]] = game
    ordered = sorted(games.values(), key=lambda g: (g["start"], g["match_id"]))
    groups = {"all_ranked": ordered, "after_train": [g for g in ordered if g["start"] > cutoff]}
    result = {"train_last_end": cutoff, "excluded": dict(excluded)}
    for name, group in groups.items():
        result[name] = {"games": len(group), "players": len(group)*10,
                        "first_start": group[0]["start"] if group else None,
                        "last_end": max((g["end"] for g in group), default=None),
                        "match_ids": [g["match_id"] for g in group],
                        "metrics": _prediction_errors(model["fit"], group)}
    return result


def load_coach_model(path=None):
    with Path(path or MODEL_PATH).open(encoding="utf-8") as src:
        bundle = json.load(src)
    if not isinstance(bundle, dict) or bundle.get("format") != "pro-replay-coach-1":
        raise ValueError("Файл не содержит поддерживаемую модель тренера.")
    return bundle


def save_coach_model(model, path):
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dest.parent, delete=False) as temp:
        name = temp.name
        try:
            json.dump(model, temp, allow_nan=False, ensure_ascii=False, indent=2)
            temp.write("\n")
        except BaseException:
            temp.close()
            Path(name).unlink(missing_ok=True)
            raise
    try:
        os.replace(name, dest)
    finally:
        Path(name).unlink(missing_ok=True)


def assess_player(model, raw_match, slot):
    """Compare independently predicted minute-20 amounts with recorded values."""
    slot = _int(slot)
    row = _player_rows(raw_match).get(slot)
    if not row or len(row["at10"]) != len(TARGETS):
        return {"status": "unavailable", "reason": "Нет полного снимка выбранного игрока на 10:00.", "metrics": []}
    if row["rank"] is None:
        return {"status": "unavailable", "reason": "Для сравнения нужны данные о золоте всех пяти союзников на 10:00.",
                "metrics": []}
    mid = _int(raw_match.get("match_id"))
    coverage = model["coverage"]
    cohort = ("train" if mid in coverage["train_match_ids"] else
              "holdout" if mid in coverage["test_match_ids"] else "external")
    metrics = []
    for key, (expected, baseline) in _forecast(model["fit"], row).items():
        _, label, unit = TARGETS[key]
        actual = row["at20"].get(key)
        if actual is not None and actual < row["at10"][key]:
            actual = None
        err = model["metrics"][key]
        metrics.append({"key": key, "label": label, "unit": unit, "expected": round(expected, 2),
                        "actual": actual, "baseline": round(baseline, 2),
                        "gap": round(actual - expected, 2) if actual is not None else None,
                        "mae": round(err["mae"], 2), "baseline_mae": round(err["baseline_mae"], 2), "method": "ridge"})
    return {"status": "ok", "role": row["rank"], "farm_rank_10": row["rank"], "cohort": cohort,
            "hero_id": row["hero"], "hero_train_n": model["fit"]["hero_counts"].get(str(row["hero"]), 0),
            "at10": row["at10"], "at20": row["at20"], "metrics": metrics}
