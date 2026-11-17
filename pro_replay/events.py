"""Timestamped player actions recorded in OpenDota logs."""

import re
from statistics import median


_LOG_FIELDS = {"kill": "kills_log", "observer": "obs_log", "sentry": "sen_log",
               "death": "deaths_log", "buyback": "buyback_log"}
_TOWER = re.compile(r"npc_dota_(goodguys|badguys)_tower([1-4])(?:_(top|mid|bot))?")
_LANES = {"top": "верхняя линия", "mid": "средняя линия", "bot": "нижняя линия"}


def _towers(match, player, hero_unit):
    """Attribute only a tower's recorded last hit to the matching hero unit."""
    objectives = match.get("objectives")
    if not player or not isinstance(objectives, list) or not isinstance(hero_unit, str):
        return [], False
    if not re.fullmatch(r"npc_dota_hero_[a-z0-9_]+", hero_unit):
        return [], False
    hero_id = player.get("hero_id")
    same_hero = [p for p in match["players"] if isinstance(p, dict) and p.get("hero_id") == hero_id]
    if hero_id is None or len(same_hero) != 1:
        return [], False

    timeline = []
    complete = match.get("objectives_complete", True) is True
    for objective in objectives:
        if not isinstance(objective, dict) or not objective.get("type"):
            complete = False
            continue
        if objective["type"] != "building_kill":
            continue
        key = objective.get("key")
        if not isinstance(key, str) or not key:
            complete = False
            continue
        tower = _TOWER.fullmatch(key)
        if not tower:
            if "_tower" in key:
                complete = False
            continue
        unit = objective.get("unit")
        if not isinstance(unit, str) or not unit or unit in {"dota_unknown", "unknown"}:
            complete = False
            continue
        if unit != hero_unit:
            continue
        if type(objective.get("time")) is not int:
            complete = False
            continue
        side, tier, lane = tower.groups()
        team = "Radiant" if side == "goodguys" else "Dire"
        detail = f"Нанёс последний удар по башне T{tier} команды {team}"
        if lane:
            detail += f" ({_LANES[lane]})"
        timeline.append({"time": objective["time"], "kind": "tower", "detail": detail})
    return timeline, complete


def event_data(match: dict, account_id: int, hero_unit: str | None = None) -> dict:
    """Return timestamped actions and separate per-kind completeness indicators.

    A missing/non-list log has unknown coverage. An empty list is an available
    log with no recorded actions. Preserve pre-horn times (negative seconds).
    """
    players = match.get("players") or []
    player = next(
        (p for p in players if isinstance(p, dict)
         and str(p.get("account_id")) == str(account_id)),
        {},
    )
    coverage = {}
    timeline = []
    for kind, field in _LOG_FIELDS.items():
        log = player.get(field)
        coverage[kind] = isinstance(log, list)
        if not coverage[kind]:
            continue
        for event in log:
            if not isinstance(event, dict) or type(event.get("time")) is not int:
                # Still display usable observations, but exclude the incomplete
                # log from estimates of the first action.
                coverage[kind] = False
                continue
            if kind == "kill":
                target = event.get("key")
                detail = "Убил героя"
                if isinstance(target, str) and target:
                    name = target.removeprefix("npc_dota_hero_").replace("_", " ").title()
                    detail = f"Убил героя {name}"
            elif kind == "observer":
                detail = "Поставил Observer Ward"
            elif kind == "sentry":
                detail = "Поставил Sentry Ward"
            elif kind == "death":
                killer = event.get("key")
                detail = "Погиб"
                if isinstance(killer, str) and killer.startswith("npc_dota_hero_"):
                    detail += " от " + killer.removeprefix("npc_dota_hero_").replace("_", " ").title()
            else:
                detail = "Использовал выкуп"
            timeline.append({"time": event["time"], "kind": kind, "detail": detail})
    slot = player.get("player_slot")
    for objective in match.get("objectives") or []:
        if not isinstance(objective, dict) or type(objective.get("time")) is not int or slot is None:
            continue
        kind = objective.get("type")
        if objective.get("player_slot") == slot and kind == "CHAT_MESSAGE_AEGIS":
            timeline.append({"time": objective["time"], "kind": "aegis", "detail": "Подобрал Aegis"})
        elif objective.get("player_slot") == slot and kind == "CHAT_MESSAGE_FIRSTBLOOD":
            timeline.append({"time": objective["time"], "kind": "firstblood", "detail": "Сделал первое убийство на карте"})
        elif (kind == "CHAT_MESSAGE_ROSHAN_KILL" and type(slot) is int
              and objective.get("team") == (2 if slot < 128 else 3)):
            timeline.append({"time": objective["time"], "kind": "roshan", "detail": "Команда игрока убила Roshan"})
    tower_events, coverage["tower"] = _towers(match, player, hero_unit)
    timeline.extend(tower_events)
    timeline.sort(key=lambda e: e["time"])
    return {"timeline": timeline, "event_coverage": coverage}


def extract_timeline(match: dict, account_id: int, hero_unit: str | None = None) -> list[dict]:
    return event_data(match, account_id, hero_unit)["timeline"]


def summarize_events(rows: list[dict]) -> dict:
    """Aggregate first recorded actions, reporting the denominator for each kind."""
    result = {}
    for kind in (*_LOG_FIELDS, "tower"):
        available = 0
        first_times = []
        early_ids = []
        for row in rows:
            if not row.get("event_coverage", {}).get(kind):
                continue
            available += 1
            times = [e["time"] for e in row["timeline"] if e["kind"] == kind]
            if not times:
                continue
            first_time = min(times)
            first_times.append(first_time)
            if first_time <= 600:
                early_ids.append(row["match_id"])
        result[kind] = {
            "available_matches": available,
            "matches_with_event": len(first_times),
            "median_first_time": median(first_times) if first_times else None,
            "before_or_at_ten_matches": len(early_ids),
            "before_or_at_ten_ids": early_ids,
        }
    return result
