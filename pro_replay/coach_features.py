"""Shared online/offline economy features, using observations at or before 10:00."""

from math import isfinite

from .coach_model import TARGETS, _game, _player_rows, _snapshot
from .impact import SLOTS


FEATURES = tuple([f"at5_{k}" for k in TARGETS] + [f"gain5to10_{k}" for k in TARGETS] + [
    "own_denies_10", "team_gold_10", "enemy_gold_10", "gold_share_10", "team_gold_lead_10", "patch"])


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _exact(player, name, second):
    times, series = player.get("times"), player.get(name)
    if not isinstance(times, list) or not isinstance(series, list):
        return None
    indices = [i for i, t in enumerate(times) if t == second]
    if len(indices) != 1 or indices[0] >= len(series):
        return None
    value = series[indices[0]]
    return value if _finite(value) and value >= 0 else None


def row_features(raw, rows=None):
    """Enrich slot-indexed rows; missing observations stay missing, never zero-filled."""
    rows = _player_rows(raw) if rows is None else rows
    players = {p.get("player_slot"): p for p in raw.get("players", []) if isinstance(p, dict)}
    for slot, row in rows.items():
        player = players.get(slot, {})
        at5 = _snapshot(player, 300, None)
        f = {key: None for key in FEATURES}
        for key in TARGETS:
            f[f"at5_{key}"] = at5.get(key)
            if key in at5 and key in row["at10"] and row["at10"][key] >= at5[key]:
                f[f"gain5to10_{key}"] = row["at10"][key] - at5[key]
        f["own_denies_10"] = _exact(player, "dn_t", 600)
        side = SLOTS[:5] if slot < 128 else SLOTS[5:]
        enemy = SLOTS[5:] if slot < 128 else SLOTS[:5]
        for name, slots in (("team_gold_10", side), ("enemy_gold_10", enemy)):
            values = [rows.get(s, {}).get("at10", {}).get("gold") for s in slots]
            if all(_finite(v) for v in values):
                f[name] = sum(values)
        if f["team_gold_10"] and "gold" in row["at10"]:
            f["gold_share_10"] = row["at10"]["gold"] / f["team_gold_10"]
        if f["team_gold_10"] is not None and f["enemy_gold_10"] is not None:
            f["team_gold_lead_10"] = f["team_gold_10"] - f["enemy_gold_10"]
        f["patch"] = raw.get("patch") if _finite(raw.get("patch")) else None
        row["features"] = f
    return rows


def game_features(raw, *, ranked=False):
    game, reason = _game(raw, ranked=ranked)
    if game is None:
        return None, reason
    rows = row_features(raw)
    game["rows"] = [rows[slot] for slot in SLOTS]
    game["patch"] = raw.get("patch")
    game["rank_tier"] = raw.get("avg_rank_tier") or raw.get("rank_tier") or raw.get("source_avg_rank_tier")
    return game, None
