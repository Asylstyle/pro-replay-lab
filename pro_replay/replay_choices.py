"""Generate tactical alternatives from a Valve demo when aggregate fight logs are sparse.

Candidates must pass the frame verifier before becoming instructions in the report.
"""

from .build_review import ITEM_INFO
from .decision_actions import SAVES
from .fight_review import SLOTS, _spell_name
from .report import _clock


# Only unit-targeted control that can directly hold a hero. Area disables need
# a point, facing or terrain analysis in addition to the hero-to-hero distance.
_CONTROL = frozenset("""
lion_voodoo shadow_shaman_voodoo shadow_shaman_shackles rubick_telekinesis
vengefulspirit_magic_missile sven_storm_bolt chaos_knight_chaos_bolt ogre_magi_fireblast
ogre_magi_unrefined_fireblast dragon_knight_dragon_tail bane_fiends_grip beastmaster_primal_roar
pudge_dismember skeleton_king_hellfire_blast enigma_malefice
item_sheepstick item_abyssal_blade
""".split())
_SAVES = {key: SAVES[key] for key in ("dazzle_shallow_grave", "abaddon_aphotic_shield",
          "oracle_false_promise", "shadow_demon_disruption", "omniknight_purification", "abaddon_death_coil")}
_SAVES.update({
    "item_glimmer_cape": "прикрыть союзника магическим барьером и, после задержки, невидимостью",
    "item_force_staff": "вытолкнуть союзника из-под удара, если он смотрел в сторону отхода",
})


def _roster(raw, heroes):
    result = {}
    for p in raw.get("players") or ():
        if not isinstance(p, dict):
            continue
        slot, hid = p.get("player_slot"), p.get("hero_id")
        if type(slot) is not int or slot not in SLOTS or type(hid) is not int:
            continue
        if slot in result:
            return {}
        hero = heroes.get(hid, {})
        if hero.get("localized_name"):
            result[slot] = (hid, hero["localized_name"])
    return result


def _key(key, kind):
    return "item_" + key.removeprefix("item_") if kind == "item" else key


def _learned_buttons(timeline, event, slot, roster, start):
    available = set()
    when = event["time"]
    for frame in timeline["frame_window"](max(0, when - 6, start), when):
        if frame["time"] > when or (frame["time"] == when and
                (type(event.get("tick")) is not int or frame["tick"] >= event["tick"])):
            continue
        heroes = frame["heroes"]
        involved = (slot, event["actor_slot"], event["target_slot"])
        if any(heroes.get(str(s), {}).get("hero_id") != roster[s][0] for s in involved):
            continue
        actor = heroes[str(slot)]
        if actor.get("alive") is not True:
            continue
        for name, state in actor.get("buttons", {}).items():
            if state.get("active") is False:
                continue
            kind = state.get("kind")
            if kind == "ability" and type(state.get("level")) is int and state["level"] > 0:
                available.add(name)
            elif kind == "item" and type(state.get("slot")) is int and 0 <= state["slot"] <= 5:
                available.add(_key(name, kind))
    return available


def _already_cast(casts, key, target_slot, start, event):
    for cast in casts:
        cast_key = cast.get("button_key")
        if cast_key not in (key, key.removeprefix("item_")) or not start <= cast["time"] <= event["time"]:
            continue
        if cast.get("target_slot") in (None, target_slot):
            return True
    return False


def _death_window(event, fights, previous):
    when = event["time"]
    matches = [f for f in fights if f["start"] <= when <= f["end"]]
    if matches:
        return min(matches, key=lambda f: (f["end"] - f["start"], f["start"])), False
    last_end = max((f["end"] for f in fights if f["end"] < when), default=-1)
    start = max(last_end + 1, when - 8, 0)
    if previous and previous["end"] >= start and previous["start"] >= last_end + 1:
        return previous, True
    return {"start": start, "end": when}, True


def replay_choices(timeline, raw, slot, heroes, fights, position):
    """Return additional death windows and frame-verification candidates for one player."""
    if not timeline or timeline.get("match_id") != raw.get("match_id"):
        return [], []
    roster = _roster(raw, heroes)
    if slot not in roster or not callable(timeline.get("frame_window")):
        return [], []
    events = timeline.get("events") or []
    casts = [e for e in events if e.get("kind") == "cast" and e.get("actor_slot") == slot]
    deaths = sorted((e for e in events if e.get("kind") == "death" and type(e.get("time")) is int and e["time"] >= 0
                     and e.get("target_slot") in roster and e.get("actor_slot") in roster
                     and e["target_slot"] // 128 == slot // 128 != e["actor_slot"] // 128),
                    key=lambda e: (e["time"], e.get("tick", 0)))
    extra, groups = [], {}
    # Group adjacent recorded deaths outside OpenDota intervals before assigning
    # bounds, so every generated link points to the same final interval as its card.
    previous = None
    for event in deaths:
        fight, native = _death_window(event, fights, previous)
        if native:
            fight["end"] = event["time"]
            previous = fight
        else:
            previous = None
        group_key = (native, fight["start"], None if native else fight["end"])
        groups.setdefault(group_key, (fight, native, []))[2].append(event)
    ranked = []
    for fight, native, losses in groups.values():
        start, end = fight["start"], fight["end"]
        messages = []
        before = len(ranked)
        for event in losses:
            when, victim, enemy = event["time"], event["target_slot"], event["actor_slot"]
            victim_name, enemy_name = roster[victim][1], roster[enemy][1]
            text = f"Последний удар по {victim_name} в повторе записан за {enemy_name}."
            messages.append({"time": when, "kind": "death" if victim == slot else "ally_death", "text": text})
            available = _learned_buttons(timeline, event, slot, roster, start)
            support = position in (4, 5)
            ordered = sorted(available & (_CONTROL | _SAVES.keys()),
                             key=lambda key: ((key in _SAVES) != support, key))
            for key in ordered:
                saving = key in _SAVES
                if saving and (victim == slot or (key in ("dazzle_shallow_grave", "oracle_false_promise")
                                                  and enemy_name == "Axe")):
                    continue
                target = victim if saving else enemy
                if _already_cast(casts, key, target, start, event):
                    continue
                button = ITEM_INFO[key[5:]][0] if key.startswith("item_") else _spell_name(key, "")
                target_name = roster[target][1]
                action = (f"Применить {button} на {target_name} до последнего удара." if saving else
                          f"Взять {enemy_name} в контроль через {button} до последнего удара по {victim_name}.")
                why = (f"Это могло {_SAVES[key]}." if saving else
                       f"Если остановить {enemy_name} до последнего удара, у {victim_name} появится время на отход.")
                decision = {"kind": "protect" if saving else "focus", "source": "replay", "requires_replay_verification": True,
                            "target_slot": target, "target": target_name, "recipient_side": "ally" if saving else "enemy",
                            "focus_slot": enemy, "enemy_focus": enemy_name, "button": button, "button_key": key,
                            "threat_key": None, "action": action, "why": why, "evidence": text,
                            "check": "Сверь направление отхода и возможность союзников продолжить действие.",
                            "strength": 2, "start": start, "end": end, "time": when, "event_time": when,
                            "event_tick": event.get("tick"), "event_kind": "death", "event_actor_slot": enemy,
                            "event_target_slot": victim, "event": text, "review_from": max(0, when - 5),
                            "timing": f"До последнего удара на {_clock(when)}.", "importance": "",
                            "pro_note": None, "pro_url": None}
                stakes = len(losses) + 2 * (victim == slot)
                ranked.append(((2, stakes, 1, 0, -start), decision))
        if native and len(ranked) > before:
            anchor = messages[-1]
            extra.append({**fight, "anchor": anchor["time"], "anchor_kind": anchor["kind"], "anchor_text": anchor["text"],
                          "source": "replay", "position": position, "events": messages, "observations": [],
                          "threats": [], "items": [], "lessons": [], "action": "Посмотри действия перед последним ударом.",
                          "check": "Интервал построен по смертям героев в файле Valve."})
    return extra, ranked
