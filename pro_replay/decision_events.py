"""Second-level hero evidence retained by the public OpenDota match response."""

from .build_review import ITEMS, ITEM_INFO
from .fight_review import SLOTS, _spell_name
from .report import _clock


def _roster(raw, heroes):
    players = raw.get("players")
    if not isinstance(players, list):
        return {}, {}
    members, units, seen = {}, {}, set()
    for p in players:
        if not isinstance(p, dict):
            continue
        slot = p.get("player_slot")
        if type(slot) is not int or slot not in SLOTS:
            continue
        if slot in seen:
            members.pop(slot, None)
            continue
        seen.add(slot)
        hero_id = p.get("hero_id")
        hero = heroes.get(hero_id, {}) if type(hero_id) is int else {}
        unit, name = hero.get("name"), hero.get("localized_name")
        members[slot] = (p, unit, name or f"Герой #{p.get('hero_id')}")
    for slot, (_, unit, _) in members.items():
        if unit:
            # A unit name alone cannot resolve duplicate heroes to one slot.
            units[unit] = None if unit in units else slot
    return members, units


def _entries(player, key):
    log = player.get(key)
    return (e for e in log if isinstance(e, dict) and type(e.get("time")) is int) if isinstance(log, list) else ()


def _side(slot, selected):
    if slot is None:
        return None
    if slot == selected:
        return "self"
    return "ally" if (slot < 128) == (selected < 128) else "enemy"


def _inventory_sample(raw, slot, item_key, second):
    if type(slot) is not int or slot not in SLOTS or type(second) is not int or item_key not in ITEM_INFO:
        return None
    wrapper = raw.get("metadata")
    data = wrapper.get("data") if isinstance(wrapper, dict) and not wrapper.get("error") else None
    match_id = raw.get("match_id")
    if type(match_id) is not int or match_id <= 0 or not isinstance(data, dict) or str(data.get("match_id")) != str(match_id):
        return None
    metadata = data.get("metadata")
    teams = metadata.get("teams") if isinstance(metadata, dict) else None
    if not isinstance(teams, list):
        return None
    side = 2 if slot < 128 else 3
    matching = [team for team in teams if isinstance(team, dict) and team.get("dota_team") == side]
    if len(matching) != 1 or not isinstance(matching[0].get("players"), list):
        return None
    players = [p for p in matching[0]["players"] if isinstance(p, dict)
               and type(p.get("player_slot")) is int and p["player_slot"] == slot]
    if len(players) != 1 or type(players[0].get("team_slot")) is not int or players[0]["team_slot"] != slot % 128:
        return None
    snapshots = players[0].get("inventory_snapshot")
    if not isinstance(snapshots, list):
        return None
    recent = [s for s in snapshots if isinstance(s, dict) and type(s.get("game_time")) is int
              and second - 30 <= s["game_time"] <= second]
    if not recent:
        return None
    last = max(s["game_time"] for s in recent)
    latest = [s.get("item_id") for s in recent if s["game_time"] == last]
    if len(latest) != 1 or not isinstance(latest[0], list) or not 1 <= len(latest[0]) <= 6:
        return None
    if any(type(i) is not int or i <= 0 for i in latest[0]):
        return None
    return last, [ITEMS.get(str(i)) for i in latest[0]]


def inventory_evidence(raw, slot, item_key, second):
    """Describe a recent main-inventory observation at its own timestamp."""
    sample = _inventory_sample(raw, slot, item_key, second)
    if sample and item_key in sample[1]:
        return f"В снимке основного инвентаря Valve на {_clock(sample[0])} у игрока есть {ITEM_INFO[item_key][0]}."
    return None


def item_present(raw, slot, item_key, second):
    """True for recent positive evidence, False only for six other items at this second.

    Applies to standard active inventory slots. A missing item in an older
    sample could have arrived from the courier or backpack in the meantime.
    """
    sample = _inventory_sample(raw, slot, item_key, second)
    if sample is None:
        return None
    stamp, items = sample
    if item_key in items:
        return True
    if stamp != second or len(items) != 6 or None in items:
        return None
    if item_key == "tpscroll" or not ITEM_INFO[item_key][1]:
        return None
    # A purchase at this same game-clock second may come after the sample.
    players = raw.get("players")
    if not isinstance(players, list):
        return None
    matches = [p for p in players if isinstance(p, dict)
               and type(p.get("player_slot")) is int and p["player_slot"] == slot]
    if len(matches) != 1 or not isinstance(matches[0].get("purchase_log"), list):
        return None
    for buy in matches[0]["purchase_log"]:
        if not isinstance(buy, dict) or type(buy.get("time")) is not int or not isinstance(buy.get("key"), str):
            return None
        if buy["time"] == second and buy["key"] == item_key:
            return None
    return False


def observed_events(raw, slot, heroes, fight):
    """Death actor is the recorded killer, death target the victim. Hits time damage, not casts."""
    start, end = fight.get("start"), fight.get("end")
    members, units = _roster(raw, heroes)
    if slot not in members or type(start) is not int or type(end) is not int or end < start:
        return []
    result, deaths = [], {}

    def event(time, kind, actor, target, text, **fields):
        subject = target if kind == "death" else actor
        return {"time": time, "kind": kind, "actor_slot": actor, "target_slot": target,
                "actor": members[actor][2] if actor is not None else None,
                "target": members[target][2] if target is not None else None,
                "perspective": _side(subject, slot), "actor_relation": _side(actor, slot),
                "target_relation": _side(target, slot), "text": text, **fields}

    for owner, (p, unit, name) in members.items():
        for field in ("deaths_log", "kills_log"):
            for entry in _entries(p, field):
                when, key = entry["time"], entry.get("key")
                if not start <= when <= end:
                    continue
                other = units.get(key) if isinstance(key, str) else None
                actor, victim = (other, owner) if field == "deaths_log" else (owner, other)
                if victim is None:
                    continue
                record = deaths.setdefault((when, victim), {"actors": set(), "sources": set(),
                                                            "time_dead": set(), "gold_lost": set()})
                record["sources"].add(field)
                if actor is not None:
                    record["actors"].add(actor)
                if field == "deaths_log":
                    for key in ("time_dead", "gold_lost"):
                        value = entry.get(key)
                        if type(value) is int and value >= 0:
                            record[key].add(value)
        for entry in _entries(p, "buyback_log"):
            if start <= entry["time"] <= end and entry.get("player_slot", owner) == owner:
                result.append(event(entry["time"], "buyback", owner, None, f"Выкуп героя {name}.",
                                    sources=["buyback_log"]))

        # This is at most one damage event per player in the entire match.
        hit = p.get("max_hero_hit")
        if not isinstance(hit, dict):
            continue
        when, value, key = hit.get("time"), hit.get("value"), hit.get("key")
        victim = units.get(key) if isinstance(key, str) else None
        if (type(when) is not int or not start <= when <= end or type(value) is not int or value <= 0
                or not unit or hit.get("unit") != unit or hit.get("player_slot", owner) != owner
                or victim is None or _side(victim, owner) != "enemy"):
            continue
        ability = hit.get("inflictor")
        ability = ability if isinstance(ability, str) and ability else None
        effect = f" от {_spell_name(ability, unit.removeprefix('npc_dota_hero_'))}" if ability else ""
        result.append(event(when, "hero_hit", owner, victim,
                            f"Урон героя {name} по {members[victim][2]} — {value}{effect}.",
                            ability=ability, damage=value, sources=["max_hero_hit"]))

    for (when, victim), record in deaths.items():
        actors = record["actors"]
        actor = next(iter(actors)) if len(actors) == 1 else None
        victim_name = members[victim][2]
        description = f"Погиб {victim_name}."
        if actor is not None:
            description = f"Последний удар по {victim_name} записан за {members[actor][2]}."
        details = {k: next(iter(record[k])) for k in ("time_dead", "gold_lost") if len(record[k]) == 1}
        result.append(event(when, "death", actor, victim, description, sources=sorted(record["sources"]), **details))
    return sorted(result, key=lambda e: (e["time"], e["kind"], e["actor_slot"] is None,
                                        e["actor_slot"], e["target_slot"] or 0))


def known_dead_at(raw, slot, heroes, second, *, before=False):
    """True only for a logged death at the cut or a still-open measured time_dead interval.

    With before=True events at the review second have not been ordered yet.
    Missing a measured death duration never implies a default respawn timer.
    """
    if type(second) is not int or type(slot) is not int:
        return False
    members, units = _roster(raw, heroes)
    if slot not in members:
        return False
    player, unit, _ = members[slot]
    for entry in _entries(player, "deaths_log"):
        start = entry["time"]
        if start > second or (before and start == second):
            continue
        duration = entry.get("time_dead")
        if start != second and (type(duration) is not int or second >= start + duration):
            continue
        # Same-second buyback and death cannot be ordered from integer timestamps.
        if not any(start <= b["time"] <= second and b.get("player_slot", slot) == slot
                   for b in _entries(player, "buyback_log")):
            return True
    if before or not unit or units.get(unit) != slot:
        return False
    if any(b["time"] == second for b in _entries(player, "buyback_log")):
        return False
    return any(e["time"] == second and e.get("key") == unit for p, _, _ in members.values()
               for e in _entries(p, "kills_log"))
