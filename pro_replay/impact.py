"""Descriptive team shares and match evidence from one OpenDota scorecard."""

from math import isfinite
from statistics import median

from .analysis import _int, extract_match
from .events import event_data
from .fight_review import extract_fights
from .patterns import find_order


# Weights are editorial choices, not coefficients learned from win/loss labels.
DIMENSIONS = (
    ("fights", "Убийства + ассисты", 25, ("kills", "assists")),
    ("damage", "Урон героям", 25, ("hero_damage",)),
    ("buildings", "Урон строениям", 20, ("tower_damage",)),
    ("control", "Секунды контроля", 15, ("stuns",)),
    ("vision", "Observer Ward поставлено + снято", 10, ("obs_placed", "observer_kills")),
    ("healing", "Лечение героев", 5, ("hero_healing",)),
)
SLOTS = (0, 1, 2, 3, 4, 128, 129, 130, 131, 132)
SEQUENCES = (
    ("Убийство после Blink Dagger за две минуты", "item:blink", "kill", 120),
    ("Последний удар по башне до Manta Style", "tower", "item:manta", None),
    ("Убийство после Observer Ward за 90 секунд", "observer", "kill", 90),
)


def amount(value):
    return value if type(value) in (int, float) and isfinite(value) and value >= 0 else None


def _total(player, fields):
    values = [amount(player.get(field)) for field in fields]
    return sum(values) if None not in values else None


def contribution(players, slot, heroes):
    """The same denominators and included weights apply to every teammate."""
    if slot not in SLOTS:
        return None
    slots = SLOTS[:5] if slot < 128 else SLOTS[5:]
    team = [p for p in players if p.get("player_slot") in slots]
    if len(team) != 5 or {p["player_slot"] for p in team} != set(slots):
        return None
    team.sort(key=lambda p: p["player_slot"])
    index = slots.index(slot)
    scores = [0.0] * 5
    metrics = []
    weight_sum = 0
    for key, label, weight, fields in DIMENSIONS:
        values = [_total(p, fields) for p in team]
        total = sum(values) if None not in values else None
        metrics.append({"key": key, "label": label, "weight": weight, "value": values[index],
                        "total": total, "share": values[index] / total if total else None,
                        "sole_leader": bool(total and values[index] == max(values) and values.count(values[index]) == 1)})
        if total:
            weight_sum += weight
            for i, value in enumerate(values):
                scores[i] += weight * value / total
    count = sum(m["share"] is not None for m in metrics)
    roster = []
    for i, player in enumerate(team):
        hero = heroes.get(player.get("hero_id"), {}).get("localized_name")
        roster.append({"slot": player["player_slot"], "name": player.get("name") or hero or "Игрок",
                       "hero": hero or f'Герой #{player.get("hero_id")}',
                       "score": scores[i] / weight_sum if count >= 3 else None,
                       "kills": amount(player.get("kills")), "deaths": amount(player.get("deaths")),
                       "assists": amount(player.get("assists")), "net_worth": amount(player.get("net_worth"))})
    return {"score": roster[index]["score"], "metrics": metrics, "roster": roster,
            "weight_sum": weight_sum, "available": count}


def match_detail(raw, player, heroes):
    players = [p for p in raw.get("players", []) if isinstance(p, dict)]
    slot = player.get("player_slot")
    side = "radiant" if slot in SLOTS[:5] else "dire" if slot in SLOTS[5:] else None
    opponents = {"radiant": "dire", "dire": "radiant"}
    stamps = player.get("times")
    series = {"lh": "lh_t", "gold": "gold_t", "xp": "xp_t", "worth": "networth_t",
              "damage": "hero_damage_t", "buildings": "tower_damage_t"}
    checkpoints = []
    duration = _int(raw.get("duration")) or 0
    if isinstance(stamps, list):
        for i, t in enumerate(stamps):
            if type(t) is not int or t <= 0 or t % 600 or t > duration:
                continue
            point = {"time": t}
            for key, field in series.items():
                values = player.get(field)
                point[key] = amount(values[i]) if isinstance(values, list) and i < len(values) else None
            checkpoints.append(point)
    review = extract_fights(raw, slot, heroes)
    fights = [{"start": f["start"], "end": f["end"], "deaths": f["deaths"], "damage": f["damage"],
               "healing": f["healing"], "gold": f["gold_delta"],
               "kills": sum(f["kills"].values()) if f["kills"] is not None else None} for f in review["intervals"]]
    teammate_rows = []
    slots = SLOTS[:5] if side == "radiant" else SLOTS[5:]
    for ally in players:
        if side is None or ally.get("player_slot") not in slots or ally["player_slot"] == slot:
            continue
        ally_id = ally.get("account_id")
        ally_row = extract_match(raw, ally_id, player=ally)
        if ally_row:
            unit = heroes.get(ally.get("hero_id"), {}).get("name")
            ally_row.update(event_data(raw, ally_id, unit, player=ally))
            teammate_rows.append(ally_row)
    ally_sequences = {}
    team_slots = [p["player_slot"] for p in players if p.get("player_slot") in slots]
    if side and sorted(team_slots) == list(slots):
        for label, before, after, limit in SEQUENCES:
            result = find_order(teammate_rows, before, after, limit)
            ally_sequences[label] = {"found": len(result["found"]), "known": len(result["found"]) + len(result["absent"])}
    return {"slot": slot, "impact": contribution(players, slot, heroes), "checkpoints": checkpoints,
            "teamfights": fights, "fights_available": review["available"], "fight_review": review,
            "ally_sequences": ally_sequences,
            "player_name": player.get("name"),
            "team_name": (raw.get(f"{side}_team") or {}).get("name"),
            "opponent": (raw.get(f"{opponents.get(side)}_team") or {}).get("name")}


def prior_maps(row, archive):
    """Keep only distinct, earlier matches on this player, hero and patch."""
    fields = ("account_id", "hero_id", "patch_version" if row.get("patch_version") else "patch")
    if not row.get("start_time") or any(row.get(k) is None for k in fields):
        return []
    return list({r["match_id"]: r for r in archive
                 if r.get("match_id") and r["match_id"] != row.get("match_id")
                 and 0 < (r.get("start_time") or 0) < row["start_time"]
                 and all(r.get(k) == row[k] for k in fields)}.values())


def observations(row, prior):
    patterns = []
    for label, before, after, limit in SEQUENCES:
        hits = find_order([row], before, after, limit)["found"]
        if hits:
            history = find_order(prior, before, after, limit)
            patterns.append({"label": label, **hits[0], "previous": len(history["found"]),
                             "comparable": len(prior) - len(history["unknown"])})
    timings = []
    def first_purchases(match):
        first = {}
        if match.get("purchase_log_available"):
            for event in sorted(match.get("core_items", []), key=lambda e: e["time"]):
                first.setdefault(event["key"], event["time"])
        return first
    older = [first_purchases(r) for r in prior]
    for key, t in first_purchases(row).items():
        samples = [items[key] for items in older if key in items]
        if samples:
            timings.append({"key": key, "time": t, "median": median(samples), "count": len(samples)})
    timings.sort(key=lambda item: -abs(item["time"] - item["median"]))
    return patterns, timings
