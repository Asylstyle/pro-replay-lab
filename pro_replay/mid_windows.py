"""Mid-lane spell decisions using one team-visible, server-timed replay frame."""

from html import escape
from functools import lru_cache
from bisect import bisect_right
from math import cos, floor, hypot, radians, sin

from .mid_spells import rank_value, spell_catalog, spell_specs
from .replay_verification import DISABLED, _bits, _control, _distance, _num, _ready, _recipient
from .report import _clock


RANGED = {2: "npc_dota_creep_goodguys_ranged", 3: "npc_dota_creep_badguys_ranged"}


def _rank(values, rank):
    value = rank_value(values, rank)
    return value if _num(value) else None


@lru_cache(maxsize=1)
def _known_spells():
    return {s["key"]: s["name"] for h in spell_catalog()["heroes"].values() for s in h["abilities"]}


def _frames(timeline, end):
    provider = timeline.get("frame_window")
    if callable(provider):
        for start in range(0, end + 1, 16):
            yield from provider(start, min(start + 15, end))
    else:
        yield from sorted((f for f in timeline.get("frames") or [] if isinstance(f, dict)
                           and type(f.get("time")) is int and 0 <= f["time"] <= end), key=lambda f: f["time"])


def _xy(entity):
    return all(_num(entity.get(k)) for k in ("x", "y"))


def _lane_landmarks(frame):
    towers = {t.get("team"): t for t in frame.get("mid_towers") or [] if isinstance(t, dict) and _xy(t)
              and t.get("name") == {2:"dota_goodguys_tower1_mid", 3:"dota_badguys_tower1_mid"}.get(t.get("team"))}
    if 2 in towers and 3 in towers:
        a, b = towers[2], towers[3]
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        length = hypot(dx, dy)
        if length > 300:
            return (a["x"], a["y"], dx / length, dy / length, length)
    return None


def _mid(entity, axis):
    if not axis or not _xy(entity):
        return False
    x, y, dx, dy, length = axis
    px, py = entity["x"] - x, entity["y"] - y
    # Restrict the review to the corridor through the two observed T1 towers.
    return -500 <= px*dx + py*dy <= length+500 and abs(px*dy-py*dx) <= 900


def _target(target, ability, team, axis):
    return (isinstance(target, dict) and target.get("takes_damage") is True and _mid(target, axis)
            and isinstance(target.get("visible_to"), dict) and target["visible_to"].get(str(team)) is True
            and _recipient(target, "enemy", ability)["state"] == "yes"
            and _num(target.get("hp")) and _num(target.get("max_hp")) and 0 < target["hp"] <= target["max_hp"])


def _amount(proof, rank, target):
    raw = _rank(proof["damage"], rank)
    if raw is None or raw <= 0:
        return None
    kind = proof["damage_type"]
    if kind == "magical":
        mr = target.get("magic_resist_pct")
        if not _num(mr) or not -100 <= mr <= 100:
            return None
        raw *= 1 - mr / 100
    elif kind == "physical":
        armor = target.get("armor")
        if not _num(armor):
            return None
        raw *= 1 - 0.06 * armor / (1 + 0.06 * abs(armor))
    elif kind != "pure":
        return None
    return floor(raw)


def _hit(actor, target, spec, rank, clean_range):
    proof = spec["proof"]
    shape = proof["shape"]
    if shape not in ("unit", "self_radius", "point", "line", "forward_circle"):
        return None
    reach = _rank(proof["reach"], rank)
    distance = _distance(actor, target)
    if not reach or distance is None:
        return None
    if shape == "forward_circle":
        yaw, offset = actor.get("facing_yaw"), _rank(proof.get("offset"), rank)
        radius = _rank(proof.get("radius"), rank)
        if not _num(yaw) or not 0 <= yaw < 360 or not offset or not radius:
            return None
        center_x = actor["x"] + offset * cos(radians(yaw))
        center_y = actor["y"] + offset * sin(radians(yaw))
        # Leave a conservative 2.8125° heading margin at the radius boundary.
        if hypot(center_x-target["x"], center_y-target["y"]) > radius - 2*offset*sin(radians(2.8125/2)) - 2:
            return None
    elif distance > reach or (not clean_range and shape != "self_radius"):
        return None
    amount = _amount(proof, rank, target)
    if not amount:
        return None
    cast_point = _rank(spec.get("cast_point"), rank)
    delay, speed = proof.get("delay"), proof.get("speed")
    delay = _rank(delay, rank) if isinstance(delay, (list,tuple)) else delay
    speed = _rank(speed, rank) if isinstance(speed, (list,tuple)) else speed
    arrival = None
    if cast_point is not None and _num(delay) and (speed is None or _num(speed) and speed > 0):
        arrival = cast_point + delay + (distance / speed if speed else 0)
    return {"damage": amount, "hp": target["hp"], "distance": round(distance), "reach":reach, "shape":shape,
            "cast_point":cast_point,
            "arrival": arrival, "instruction":proof.get("instruction","")}


def _attack_window(frame, actor, rival, axis, team):
    if (not isinstance(rival, dict) or not _mid(actor, axis) or actor.get("alive") is not True
            or not _target(rival, "", team, axis) or _bits(actor, (*DISABLED, 1)) is not False
            or _bits(rival,(2,)) is not False):
        return None
    level, enemy_level = actor.get("level"), rival.get("level")
    reach, distance = actor.get("attack_range"), _distance(actor, rival)
    if (type(level) is not int or type(enemy_level) is not int or not 1 <= enemy_level < level <= 30
            or not _num(reach) or not reach > 0 or distance is None or distance > reach):
        return None
    if (not _num(actor.get("hp")) or not _num(actor.get("max_hp")) or not 0 < actor["hp"] <= actor["max_hp"]
            or actor["hp"] / actor["max_hp"] < rival["hp"] / rival["max_hp"]):
        return None
    return {"time":frame["time"],"level":level,"opponent_level":enemy_level,
            "distance":round(distance),"attack_range":round(reach)}


def _recent_cast(casts, slot, key, frame):
    return next((c["time"] for c in casts if c.get("actor_slot") == slot and c.get("button_key") == key
                 and frame["tick"] <= c["tick"] and 0 <= c["time"]-frame["time"] <= 2), None)


def _spell_windows(frame, actor, rival, axis, specs, ctx):
    slot, team, clean_range, casts = ctx
    if (not _mid(actor, axis) or actor.get("alive") is not True or type(actor.get("level")) is not int
            or not 1 <= actor["level"] <= 30 or _control(actor, False, True)["state"] != "yes"):
        return
    creeps = [c for c in frame.get("ranged_creeps") or [] if isinstance(c, dict) and type(c.get("id")) is int
              and c.get("team") == 5-team and c.get("name") == RANGED[5-team]]
    for key, spec in specs.items():
        proof = spec.get("proof")
        button = (actor.get("buttons") or {}).get(key)
        if not proof or not isinstance(button, dict) or button.get("kind") != "ability":
            continue
        rank = button.get("level")
        if type(rank) is not int or not 1 <= rank <= spec["max_rank"] or any(check["state"] != "yes" for check in _ready(actor, button, False)):
            continue
        health_cost = _rank(spec.get("health_cost") or [0], rank)
        if (health_cost is None or health_cost > 0 and (not _num(actor.get("hp")) or actor["hp"] <= health_cost)
                or key in ("sandking_burrowstrike","morphling_waveform","puck_waning_rift") and _bits(actor, (0,)) is not False):
            continue
        base = {"time": frame["time"], "game_time": frame.get("game_time", frame["time"]),
                "spell": spec["name"], "key": key, "rank": rank, "level": actor["level"],
                "mana": actor["mana"], "cost": button["mana_cost"], "health_cost":health_cost,
                "recorded_cast": _recent_cast(casts, slot, key, frame)}
        enemy_hit = None
        if proof["target"] in ("both", "hero") and _target(rival, key, team, axis):
            enemy_hit = _hit(actor, rival, spec, rank, clean_range)
            their_level = rival.get("level")
            ours = actor.get("hp")
            if type(their_level) is int and 1 <= their_level <= 30 and _num(ours) and _num(actor.get("max_hp")) and actor["max_hp"] >= ours > 0:
                level_lead = actor["level"] - their_level
                hp_lead = ours / actor["max_hp"] - rival["hp"] / rival["max_hp"]
                if enemy_hit and (level_lead > 0 or hp_lead >= .15):
                    yield {**base, **enemy_hit, "kind":"trade", "opponent_level":their_level,
                           "hp_pct":round(100*ours/actor["max_hp"]),
                           "opponent_hp_pct":round(100*rival["hp"]/rival["max_hp"])}
        if proof["target"] not in ("both", "creep"):
            continue
        for c in creeps:
            if not _target(dict(c, alive=True), key, team, axis):
                continue
            hit = _hit(actor, c, spec, rank, clean_range)
            if hit and hit["damage"] >= c["hp"]:
                # Self-centered and forward circles share a single fixed area at this frame.
                # Unit-target spells with uncertain bounce choices are excluded from this cue.
                joint = enemy_hit is not None and proof["shape"] in ("self_radius","forward_circle")
                yield {**base, **hit, "kind":"creep", "id":c["id"], "joint_hero":joint}


def _shortlist(rows, kind):
    kept, identities = [], set()
    for row in rows:
        if row["kind"] != kind or any(abs(a["time"]-row["time"]) < 35 for a in kept):
            continue
        identity = row["id"] if kind == "creep" else (row["level"], row["key"])
        if identity in identities:
            continue
        kept.append(row)
        identities.add(identity)
        if len(kept) == 3:
            break
    return kept


def mid_decision_windows(raw, slot, matchup, timeline, *, catalog=None):
    if not matchup or matchup.get("status") != "available":
        return None
    missing = {"reason":"Для секундных окон нужны кадры повтора Valve с ранней средней линией."}
    if (not isinstance(timeline, dict) or timeline.get("format") != "pro-replay-truth-v1"
            or type(raw.get("match_id")) is not int or raw["match_id"] <= 0 or timeline.get("match_id") != raw["match_id"]
            or matchup.get("patch") != spell_catalog()["patch"] or timeline.get("patch_version") not in (None, matchup["patch"])):
        return missing
    ours, theirs = matchup["self"], matchup["opponent"]
    enemy = theirs.get("slot")
    if type(slot) is not int or ours.get("slot") != slot or type(enemy) is not int or (slot < 128) == (enemy < 128):
        return missing
    if catalog is None:
        catalog = spell_specs(ours["id"])
    names = _known_spells() | {key:s["name"] for key,s in catalog.items()}
    team = 2 if slot < 128 else 3
    events = [e for e in timeline.get("events") or () if isinstance(e, dict) and type(e.get("time")) is int
              and 0 <= e["time"] <= 600 and type(e.get("tick")) is int and e.get("actor_slot") == slot]
    casts = [e for e in events if e.get("kind") == "cast"]
    last_hits = [{"time":e["time"], "spell":names[e["button_key"]]}
                 for e in events if e.get("kind") == "ranged_last_hit" and e.get("target_name") == RANGED[5-team]
                 and isinstance(e.get("button_key"), str) and e["button_key"] in names]
    roster = [p.get("hero_id") for p in raw.get("players") or [] if isinstance(p, dict)]
    enemy_bane = any(p.get("hero_id") == 3 and type(p.get("player_slot")) is int
                     and (p["player_slot"] < 128) != (slot < 128) for p in raw.get("players") or () if isinstance(p,dict))
    clean_range = len(roster) == len(set(roster)) == 10 and not enemy_bane
    end = raw.get("duration")
    if type(end) is not int or end <= 0:
        return missing
    rows, longevity, mid_frames, sampled, axis = [], {}, {}, 0, None
    creep_frames = {}
    attacks = []
    for f in _frames(timeline, min(end, 600)):
        if type(f.get("tick")) is not int or type(f.get("time")) is not int:
            continue
        axis = _lane_landmarks(f) or axis
        h = f.get("heroes")
        if not isinstance(h, dict) or not axis or not isinstance(f.get("ranged_creeps"), list):
            continue
        actor, rival = h.get(str(slot)), h.get(str(enemy))
        if not isinstance(actor, dict) or actor.get("hero_id") != ours["id"]:
            continue
        if not isinstance(rival, dict) or rival.get("hero_id") != theirs["id"]:
            rival = None
        if rival and _mid(actor, axis) and _mid(rival, axis):
            mid_frames[f["time"]] = f["tick"]
        if len(attacks) < 2 and (not attacks or f["time"] - attacks[-1]["time"] >= 45):
            attack = _attack_window(f, actor, rival, axis, team)
            if attack and all(x["level"] != attack["level"] for x in attacks):
                attacks.append(attack)
        sampled += 1
        when = f.get("game_time", f["time"])
        if _num(when):
            creep_frames[when] = set()
        for c in f["ranged_creeps"]:
            if isinstance(c, dict) and c.get("team") == 5-team and c.get("name") == RANGED[5-team] and type(c.get("id")) is int and _num(c.get("hp")) and c["hp"] > 0:
                # Positive later observation of this exact serial-qualified entity.
                if _num(when):
                    creep_frames[when].add(c["id"])
                    longevity[c["id"]] = when
        rows.extend(_spell_windows(f, actor, rival, axis, catalog, (slot,team,clean_range,casts)))
    if not sampled and not last_hits:
        return missing
    sample_times = sorted(creep_frames)
    for row in rows:
        if row["kind"] == "creep":
            row["alive_after_estimate"] = (row["arrival"] is not None and
                longevity.get(row["id"], -1) >= row["game_time"] + row["arrival"])
            # If the exact unit is already absent from a complete subsequent
            # frame before the cast point finishes, drop that speculative window.
            # A unit disappearing after the cast is not proof of who got the CS.
            stop = row["game_time"] + (row["cast_point"] or 0)
            start_idx, end_idx = bisect_right(sample_times, row["game_time"]), bisect_right(sample_times, stop)
            row["lost_before_release"] = any(row["id"] not in creep_frames[t] for t in sample_times[start_idx:end_idx])
    # Prefer time windows where the ranged creep was still alive after the
    # nominal delay. This is explicitly described as an after-the-fact check.
    creeps = sorted((r for r in rows if r["kind"] == "creep" and not r["lost_before_release"]),
                    key=lambda r: (not r["alive_after_estimate"], not r["joint_hero"], r["time"]))
    hits = []
    for e in sorted(events, key=lambda e:e["tick"]):
        if (e.get("kind") == "spell_damage" and e.get("target_slot") == enemy and e.get("button_key") in names
                and _num(e.get("damage")) and e["damage"] > 0 and
                any(t in mid_frames and mid_frames[t] <= e["tick"] for t in (e["time"], e["time"]-1))
                and (not hits or e["time"] - hits[-1]["time"] >= 30)):
            hits.append({"time":e["time"], "spell":names[e["button_key"]], "damage":e["damage"]})
            if len(hits) == 3:
                break
    return {"seconds":sampled, "enemy":theirs["name"], "trades":_shortlist(rows,"trade"), "hero_hits":hits, "attacks":attacks,
            "creeps":sorted(_shortlist(creeps,"creep"), key=lambda r:r["time"]),
            "last_hits":last_hits, "reviewable_spells":sum(bool(s.get("proof")) for s in catalog.values())}


def render_mid_windows(review):
    if not review:
        return ""
    parts = ['<section id="mid-windows"><h2>Мид по секундам</h2>']
    if review.get("reason"):
        return "".join(parts) + f'<p>{escape(review["reason"])}</p></section>'
    parts.append("<p>Первые десять минут, вблизи линии между средними T1. "
                 "Прогноз урона учитывает один описанный эффект и базовую защиту из кадра. "
                 "Барьер, усиление урона, поворот, движение цели и летящие атаки могут изменить результат.</p>")
    if not review["reviewable_spells"]:
        parts.append("<p>Для этого героя в справочнике пока нет численно проверенного отдельного попадания. "
                     "По повтору доступны отмеченные в журнале попадания и добивания.</p>")
    for kind, title in (("trades","Когда предложить размен"),("creeps","Дальний крип под нюк")):
        parts.append(f"<h3>{title}</h3>")
        if not review[kind]:
            parts.append("<p>Подходящее сочетание состояния кнопки, видимости, дистанции и цели не подтверждено.</p>")
        for r in review[kind]:
            lead = (f'{escape(review["enemy"])} на уровне {r["opponent_level"]}. Здоровье сторон '
                    f'{r["hp_pct"]}% и {r["opponent_hp_pct"]}%. Сравни урон нюка с возможным ответом соперника. '
                    if kind == "trades" else f'У вражеского дальнего крипа {r["hp"]} HP. ')
            parts.append(f'<article><h4>{_clock(r["time"])} · уровень {r["level"]} · {escape(r["spell"])}, '
                         f'ранг {r["rank"]}</h4><p>{lead}Цель видна на дистанции {r["distance"]}. '
                         f'Кнопка готова, в запасе {r["mana"]:.0f} маны, расход {r["cost"]:.0f}. '
                         f'Расчётный урон — {r["damage"]}.')
            if r["health_cost"]:
                parts.append(f' Расход здоровья — {r["health_cost"]:g}.')
            if kind == "creeps" and r["arrival"] is not None:
                parts.append(f' Ориентир до попадания — {r["arrival"]:.2f} с без поворота. ')
                parts.append("Последующий кадр подтверждает, что этот крип дожил до расчётного времени."
                             if r["alive_after_estimate"] else "Следующий секундный кадр не подтверждает, как долго этот крип прожил.")
            if r.get("joint_hero"):
                parts.append(f' В этом же кадре {escape(review["enemy"])} тоже стоял в области того же нажатия. '
                             'Проверь, сохранилась ли возможность одним нюком взять крипа и задеть героя.')
            if r["recorded_cast"] is not None:
                parts.append(f' В {_clock(r["recorded_cast"])} журнал уже фиксирует применение этой кнопки.')
            if r.get("instruction"):
                parts.append(" " + escape(r["instruction"]))
            parts.append("</p></article>")
    if review["attacks"]:
        parts.append("<h3>Преимущество уровня на дистанции атаки</h3>")
        for a in review["attacks"]:
            parts.append(f'<p>{_clock(a["time"])} · уровень {a["level"]} против {a["opponent_level"]}, '
                         f'соперник виден в {a["distance"]} ед., дальность атаки {a["attack_range"]}. '
                         'Сравни короткий удар и отход с добиванием волны. Остаток интервала между автоатаками, '
                         'башню, ответ саппорта и летящие атаки проверь в повторе.</p>')
    if review["hero_hits"]:
        observed = ", ".join(f'{_clock(e["time"])} · {escape(e["spell"])} ({e["damage"]} урона)' for e in review["hero_hits"])
        parts.append(f'<h3>Что уже попало по мидеру</h3><p>{observed}.</p><p>Это время попадания из журнала. '
                     'Периодический эффект может сработать спустя несколько секунд после применения.</p>')
    if review["last_hits"]:
        hits = ", ".join(f'{_clock(e["time"])} · {escape(e["spell"])}' for e in review["last_hits"])
        parts.append(f"<h3>Фактические добивания способностью</h3><p>{hits}.</p>"
                     "<p>Журнал смерти назвал героя и способность для вражеского дальнего крипа "
                     "в первые десять минут. В этой записи указано имя типа крипа, без его координат.</p>")
    return "".join([*parts,"</section>"])
