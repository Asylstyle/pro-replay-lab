"""Earlier, team-visible packets for ready lane skills without a full damage model."""

from collections import defaultdict
from html import escape

from .mid_spells import rank_value, resolved_impact
from .replay_verification import _control, _num, _ready
from .report import _clock


def _comparable(spec):
    proof = spec.get("proof")
    return (spec.get("mechanic") not in ("innate", "passive", "summon")
            and (not proof or proof.get("partial")))


def _unique_hero(unit, buttons):
    # The combat log names a hero, not that hero's particular clone handle.
    clone_key = {82:"meepo_divided_we_stand", 113:"arc_warden_tempest_double"}.get(unit["hero_id"])
    if not clone_key:
        return True
    clone = buttons.get(clone_key)
    return isinstance(clone, dict) and clone.get("level") == 0


def _skills(unit, catalog):
    buttons = unit.get("buttons")
    if not isinstance(buttons, dict) or not _unique_hero(unit, buttons):
        return None
    if not isinstance(unit.get("name"), str) or not unit["name"].startswith("npc_dota_hero_"):
        return None
    ranks, signatures, ready = {}, {}, set()
    free = _control(unit, False, True)["state"] == "yes"
    for key, spec in catalog.items():
        b = buttons.get(key)
        if not _comparable(spec) or not isinstance(b, dict) or b.get("kind") != "ability":
            continue
        rank = b.get("level")
        if type(rank) is not int or not 1 <= rank <= spec["max_rank"]:
            continue
        signature = (rank,)
        if unit["hero_id"] == 74:
            # Invoked abilities stay at displayed rank 1 while their effects
            # scale with independently learned orb ranks.
            learned = [buttons.get(orb) for orb in ("invoker_quas","invoker_wex","invoker_exort")]
            if any(not isinstance(b,dict) or b.get("kind") != "ability" for b in learned):
                continue
            orbs = [b.get("level") for b in learned]
            if any(type(n) is not int or not 0 <= n <= 8 for n in orbs):
                continue
            signature = (rank,*orbs)
        ranks[key], signatures[key] = rank, signature
        if not free or any(check["state"] != "yes" for check in _ready(unit,b,False)):
            continue
        if unit["hero_id"] == 74 and key.startswith("invoker_") and b.get("slot") not in (3,4):
            continue
        cost = rank_value(spec.get("health_cost") or [0],rank)
        if not _num(cost) or cost > 0 and (not _num(unit.get("hp")) or unit["hp"] <= cost):
            continue
        if spec.get("proof") and resolved_impact(spec, unit, rank) is None:
            continue
        ready.add(key)
    return {"name":unit["name"], "hero_id":unit["hero_id"], "ranks":ranks, "signatures":signatures, "ready":ready}


def lane_sample(frame, units, catalogs, team):
    """Keep only identity and learned/ready skills; the caller verifies lane geometry."""
    if not _num(frame.get("game_time")) or type(frame.get("tick")) is not int:
        return None
    for slot, unit in units.items():
        other_team = 3 if slot < 128 else 2
        visible = unit.get("visible_to")
        if (unit.get("alive") is not True or not isinstance(visible,dict) or visible.get(str(team)) is not True
                or visible.get(str(other_team)) is not True):
            return None
    heroes = {slot:_skills(unit,catalogs[slot]) for slot,unit in units.items()}
    if not all(heroes.values()):
        return None
    return {"time":frame["time"], "tick":frame["tick"], "clock":frame["game_time"], "heroes":heroes}


def _bracket(samples, event):
    """Both chronological clocks must place the event between the same adjacent samples."""
    tick, clock, second = event["tick"], event["game_time"], event["time"]
    for sec in (second, second-1):
        before, after = samples.get(sec), samples.get(sec+1)
        if (before and after and before["tick"] <= tick <= after["tick"]
                and before["clock"] <= clock <= after["clock"]):
            return sec
    return None


def annotate_prior_hits(cards, events, samples, ours, opponent, catalogs):
    """Annotate after decisions are ranked; historical damage never determines the verdict."""
    for card in cards:
        card["prior_hits"] = []
    if not cards or not samples:
        return
    pair = {ours:opponent, opponent:ours}
    valid = [e for e in events if isinstance(e,dict) and e.get("kind") in ("cast","spell_damage")
             and type(e.get("actor_slot")) is int and e["actor_slot"] in pair and isinstance(e.get("button_key"),str)
             and e["button_key"] in catalogs[e["actor_slot"]] and type(e.get("tick")) is int
             and type(e.get("time")) is int and _num(e.get("game_time")) and 0 <= e["time"] <= 600]
    casts, packets = {}, defaultdict(list)
    for e in sorted(valid,key=lambda v:(v["tick"],v["game_time"],v["kind"]!="cast")):
        source, key = e["actor_slot"], e["button_key"]
        # Even an unobserved intervening cast invalidates the previous link.
        sec = _bracket(samples,e)
        if e["kind"] == "cast":
            casts[source,key] = (sec, e["game_time"])
            continue
        target = pair[source]
        if (e.get("direct_hero_source") is not True or type(e.get("target_slot")) is not int or e["target_slot"] != target or sec is None
                or not _num(e.get("damage")) or e["damage"] <= 0 or (source,key) not in casts):
            continue
        start, cast_time = casts[source,key]
        if start is None or start > sec or cast_time > e["game_time"]:
            continue
        target_name = samples[sec]["heroes"][target]["name"]
        if (e.get("target_name") != target_name or e.get("attacker_illusion") or e.get("target_illusion")
                or e.get("actor_name",samples[sec]["heroes"][source]["name"]) != samples[sec]["heroes"][source]["name"]):
            continue
        signature = samples[sec]["heroes"][source]["signatures"].get(key)
        if not signature:
            continue
        # If a rank changed in flight, or vision/identity was lost, the
        # displayed rank is not reliable for this damage application.
        if any(s not in samples or samples[s]["heroes"][source]["signatures"].get(key) != signature
               or samples[s]["heroes"][target]["name"] != target_name for s in range(start,sec+2)):
            continue
        packets[source,key,signature].append((e["tick"],e["game_time"],samples[sec+1]["tick"],e["damage"]))
    for card in cards:
        now = samples.get(card["time"])
        if not now or now["tick"] != card["tick"]:
            continue
        for source in (opponent,ours):
            unit = now["heroes"][source]
            for key in sorted(unit["ready"]):
                rank = unit["ranks"][key]
                prior = [p for p in packets.get((source,key,unit["signatures"][key]),()) if p[0] < now["tick"] and p[1] < now["clock"]
                         and p[2] <= now["tick"]]
                if prior:
                    values = [p[3] for p in prior]
                    card["prior_hits"].append({"side":"enemy" if source == opponent else "self",
                        "source_slot":source,"target_slot":pair[source],"key":key,"spell":catalogs[source][key]["name"],
                        "rank":rank,"count":len(prior),"damage_min":min(values),"damage_max":max(values),
                        "last_time":prior[-1][1],"last_tick":prior[-1][0]})


def render_prior_hits(rows, enemy):
    if not rows:
        return ""
    parts = ['<details><summary>Какой урон этот навык уже наносил на линии</summary><ul>']
    for r in rows:
        who = escape(enemy) if r["side"]=="enemy" else "Твой герой"
        damage = str(r["damage_min"]) if r["damage_min"] == r["damage_max"] else f'{r["damage_min"]}–{r["damage_max"]}'
        parts.append(f'<li>{who} · {escape(r["spell"])}, ранг {r["rank"]}. '
                     f'Записей урона по тому же мидеру — {r["count"]}, по {damage} HP. '
                     f'Последняя — {_clock(int(r["last_time"]))}.</li>')
    parts.append('</ul><p>Ранг измерен по кадрам вокруг применения и попадания. Журнал может записывать каждый тик отдельно. '
                 'Для следующего применения снова важны позиция, защита и условия навыка.</p></details>')
    return "".join(parts)
