"""One-action mid-lane comparisons at the positions in a single replay frame."""

from html import escape

from .mid_spells import rank_value
from .mid_prior_hits import render_prior_hits
from .mid_support import STATE_PRIORITY, render_support
from .replay_verification import _control, _distance, _num, _ready
from .report import _clock


# A short exchange compares one order per hero. A near-ready button and an
# order already in flight can both change the first response.
REPLY_HORIZON = 1.5
MARGIN = 2.0  # percentage points of each hero's maximum HP, a coaching heuristic

# These spells can interrupt, displace, or prevent a return hit. For spells
# with a damage model we only flag control when the same geometry also hits.
CONTROL = {
    "sandking_burrowstrike", "lina_light_strike_array", "leshrac_split_earth",
    "earthshaker_fissure", "lion_impale", "puck_waning_rift", "nevermore_requiem",
    "tiny_avalanche", "centaur_hoof_stomp", "sven_storm_bolt", "rubick_telekinesis",
    "lion_voodoo", "shadow_shaman_voodoo", "shadow_shaman_shackles", "dragon_knight_dragon_tail",
    "bane_nightmare", "bane_fiends_grip", "beastmaster_primal_roar", "chaos_knight_chaos_bolt",
    "obsidian_destroyer_astral_imprisonment", "invoker_cold_snap", "invoker_tornado",
    "invoker_deafening_blast", "earth_spirit_boulder_smash", "huskar_inner_fire", "slardar_slithereen_crush",
    "windrunner_shackleshot", "pudge_dismember", "primal_beast_pulverize", "phoenix_supernova", "necrolyte_reapers_scythe",
}
# Unit-target control without a single-hit estimate. For these specific
# actions the hero's cast range applies directly to the visible other hero.
UNIT_CONTROL = {
    "rubick_telekinesis", "shadow_shaman_voodoo", "lion_voodoo", "shadow_shaman_shackles",
    "dragon_knight_dragon_tail", "bane_nightmare", "bane_fiends_grip",
    "beastmaster_primal_roar", "chaos_knight_chaos_bolt", "invoker_cold_snap",
    "windrunner_shackleshot", "pudge_dismember", "primal_beast_pulverize", "necrolyte_reapers_scythe",
}
DEFENSIVE = {
    "puck_phase_shift", "puck_ethereal_jaunt", "queenofpain_blink", "antimage_blink",
    "antimage_counterspell", "templar_assassin_refraction", "ember_spirit_flame_guard",
    "tinker_defense_matrix", "necrolyte_sadist", "juggernaut_blade_fury",
    "void_spirit_dissimilate", "storm_spirit_ball_lightning", "windrunner_windrun", "phoenix_supernova",
}
PERSISTENT = {"templar_assassin_refraction", "ember_spirit_flame_guard", "tinker_defense_matrix",
              "phoenix_supernova", "windrunner_windrun"}
NON_ORDERS = {"invoker_quas", "invoker_wex", "invoker_exort", "twin_gate_portal_warp"}
MOVES_CASTER = {"sandking_burrowstrike", "morphling_waveform", "puck_waning_rift", "pangolier_shield_crash",
                "ursa_earthshock", "tusk_snowball", "void_spirit_dissimilate", "meepo_poof"}


def _compact(row):
    return {"key":row["key"], "spell":row["spell"], "damage_min":row.get("damage_min", row.get("damage")),
            "damage_max":row.get("damage_max", row.get("damage")), "cost":row.get("cost", 0),
            "health_cost":row.get("health_cost", 0), "ready":row.get("ready", True),
            "partial":bool(row.get("partial")), "defense_known":row.get("defense_known", True),
            "distance":row.get("distance"), "arrival":row.get("arrival"), "rank":row.get("rank"),
            "note":row.get("note") or row.get("instruction") or "", "timed_spell":"cast_point" in row}


def response_profile(enemy, actor, specs, spell_hits, attacks):
    """Only called with two visible heroes. CD and mana are spectator evidence."""
    plausible = [_compact(r) for r in [*spell_hits, *attacks]]
    measured = {r["key"]:r for r in plausible}
    controls, unknown, blocked = [], [], []
    buttons = enemy.get("buttons")
    if not isinstance(buttons, dict) or not buttons:
        return {"best":None, "controls":[], "unknown":["нет состояния кнопок соперника"], "blocked":[]}
    d = _distance(enemy, actor)
    if any(r["partial"] or not r["defense_known"] or r["key"] in ("huskar_burning_spear", "viper_poison_attack")
           for r in plausible):
        unknown.append("часть урона или защиты для ответа не измерена")
    if any(r.get("timed_spell") and (r["arrival"] is None or r["arrival"] > REPLY_HORIZON) for r in plausible):
        unknown.append("момент попадания ответного навыка выходит за пределы короткого размена или неизвестен")
    if d is None or not _num(enemy.get("attack_range")):
        unknown.append("неизвестна дистанция обычной атаки")
    elif d <= enemy["attack_range"] and not attacks:
        unknown.append("урон или условия обычной атаки не установлены")
    control_state = _control(enemy, False, True)["state"]
    if control_state != "yes":
        unknown.append("соперник под контролем или уже выполняет другое действие")
    for key, spec in specs.items():
        if key in NON_ORDERS or spec.get("mechanic") in ("innate", "passive"):
            continue
        b = buttons.get(key)
        name = spec["name"]
        if not isinstance(b, dict) or b.get("kind") != "ability":
            gates = spec.get("rank_gates") or ()
            if gates and type(enemy.get("level")) is int and gates[0] <= enemy["level"]:
                unknown.append(name + " — ранг неизвестен")
            continue
        if b.get("level") == 0 or b.get("active") is False:
            continue
        if key == "pudge_rot" and b.get("toggled") is True:
            radius = next((rank_value(p["values"],b["level"]) for p in spec["parameters"] if p["field"]=="rot_radius"), None)
            if d is None or not _num(radius) or d <= radius:
                unknown.append(name + " уже включён. Учти следующие тики")
            continue
        checks = _ready(enemy, b, False)
        states = [c["state"] for c in checks]
        cd = b.get("cooldown")
        if "no" in states:
            if b.get("in_phase") or b.get("channeling") or key in PERSISTENT:
                unknown.append(name + " — проверь уже начатое действие или действующий эффект")
            elif _num(cd) and cd > 0:
                blocked.append({"spell":name, "cooldown":cd})
                if cd <= REPLY_HORIZON:
                    unknown.append(name + " скоро восстановится")
            elif checks[-1]["state"] == "no":
                blocked.append({"spell":name, "mana_short":True})
            continue
        if "unknown" in states:
            unknown.append(name + " — готовность неизвестна")
            continue
        if control_state != "yes":
            continue
        if key in measured:
            if key in CONTROL:
                controls.append(name)
            if key in MOVES_CASTER:
                unknown.append(name + " меняет положение соперника")
            continue
        if key in DEFENSIVE:
            unknown.append(name + " — возможна защита или уход")
            continue
        if key in UNIT_CONTROL:
            reach = rank_value(spec.get("range"), b["level"])
            if _num(reach) and d is not None and d <= reach:
                controls.append(name)
            else:
                unknown.append(name + " — эффективная дальность контроля не установлена")
            continue
        # A ready spell with unpriced damage, or a target just beyond the
        # known baseline reach, cannot be silently treated as zero retaliation.
        proof = spec.get("proof")
        if not proof:
            unknown.append(name + " — ответ зависит от механики навыка")
        elif proof.get("target") != "creep":
            reach = rank_value(proof.get("reach"), b["level"])
            shape = proof.get("shape")
            if shape == "forward_circle" and d is not None:
                offset = rank_value(proof.get("offset"), b["level"])
                if _num(offset) and _num(reach) and max(0, offset-reach) <= d <= offset+reach:
                    unknown.append(name + " — соперник может развернуться")
            elif (not _num(reach) or d is None or d <= reach or shape in ("unit","point","line")):
                unknown.append(name + " — попадание ответом не установлено")
    for key, b in buttons.items():
        if (key not in specs and key not in NON_ORDERS and isinstance(b, dict) and b.get("kind") == "ability"
                and b.get("active") is True and _num(b.get("level")) and b["level"] > 0):
            unknown.append(key + " — дополнительный навык вне набора героя")
    return {"best":max(plausible, key=lambda r:r["damage_max"], default=None),
            "controls":sorted(set(controls)), "unknown":list(dict.fromkeys(unknown)),
            "blocked":sorted(blocked,key=lambda x:-x.get("cooldown",0))}


def exchange_card(frame):
    max_hp, enemy_max = frame["own_max"], frame["enemy_max"]
    if not all(_num(x) and x > 0 for x in (max_hp, enemy_max, frame["own_hp"])):
        return None
    options = [_compact(r) for r in frame["hero"]]
    own = max(options, key=lambda r:(r["ready"] is True, r["defense_known"],
               r["damage_min"]/enemy_max-r["health_cost"]/max_hp), default=None)
    # Shared spell rows have already been validated against subsequent creep
    # samples for impossible casts. An unknown survival time stays explicit.
    creeps = [r for r in frame["creeps"] if not r.get("lost_before_release")]
    target = max(creeps, key=lambda r:(r.get("alive_after_estimate",False), r.get("ready",True) is True,
                                        r["joint_hero"], -r.get("health_cost",0), -r.get("cost",0)), default=None)
    creep = None if target is None else {**_compact(target), "hp":target["hp"], "id":target["id"],
            "joint_hero":target["joint_hero"], "alive_after_estimate":target.get("alive_after_estimate",False)}
    if own is None and creep is None:
        return None
    reply = frame["reply"]
    best = reply["best"]
    dangerous = bool(reply["controls"]) or (best is not None and
                 best["damage_max"] + (own["health_cost"] if own else 0) >= frame["own_hp"])
    joint = next((r for r in options if creep and creep["joint_hero"] and r["key"] == creep["key"]
                  and creep["ready"] is True and creep["alive_after_estimate"] and r["key"] not in MOVES_CASTER
                  and not dangerous and not frame["tower_nearby"] and not frame["extra_hero"]), None)
    if joint:
        own = joint
    outgoing = 100 * min(own["damage_min"],frame["enemy_hp"]) / enemy_max if own else None
    incoming = (100 * ((best["damage_max"] if best else 0) + (own["health_cost"] if own else 0)) / max_hp
                if best or not (reply["unknown"] or reply["controls"]) else None)
    margin = outgoing-incoming if own and incoming is not None else None
    caution = list(reply["unknown"])
    if frame["tower_nearby"]:
        caution.append("игрок в 850 ед. от вражеской средней T1")
    if frame["extra_hero"]:
        caution.append("в радиусе 850 ед. виден ещё один враг")
    if own and (not own["defense_known"] or own["key"] in MOVES_CASTER):
        caution.append("после применения изменится позиция героя или неизвестна полная защита цели")
    if own and own["partial"]:
        caution.append("оценён один компонент навыка. Длительность и итог действия требуют проверки")
    if own and own["timed_spell"] and (own["arrival"] is None or own["arrival"] > REPLY_HORIZON):
        caution.append("попадание своего навыка требует дополнительного времени")
    contested = (own and creep and creep["ready"] is True and own["key"] == creep["key"]
                 and not creep["joint_hero"] and own["damage_min"] < frame["enemy_hp"]
                 and margin is not None and MARGIN < margin <= 2*MARGIN)
    if joint:
        action, reason = "both", "Мидер и дальний крип находятся в одной области. Примени навык с этой позиции и проверь возможный ответ."
    elif contested:
        action, reason = "creep", "Одна кнопка нужна для двух разных целей. Одного попадания по мидеру недостаточно для убийства, а перевес первого удара не больше четырёх процентных пунктов."
    elif creep and (dangerous or margin is not None and margin <= MARGIN or not own):
        action = "creep"
        reason = ("У соперника есть контроль или потенциально смертельный ответ." if dangerous else
                  "Перевес в доле здоровья за первое действие мал или на стороне противника." if own else
                  "Для добивания хватает урона. Применение по мидеру в этом кадре не подтверждено.")
    elif own and own["ready"] is True and margin is not None and margin > MARGIN and not dangerous and not caution:
        action, reason = "trade", "Твоё попадание снимало большую долю здоровья, чем сильнейший рассчитанный одиночный ответ."
    elif creep:
        action, reason = "creep", "Урона на крипа хватает. Преимущество при обмене ударами не установлено."
    else:
        action, reason = "uncertain", "По этому кадру перевеса для короткого размена не установлено."
    support = frame.get("support") or {}
    coordinated = bool(support.get("state") == "ready" and support.get("target_disabled") is False
                       and own and own["ready"] is True
                       and action != "creep" and not dangerous and not caution
                       and _num(own["arrival"]) and own["arrival"] < support["button"]["duration"]
                       and support["button"]["arrival"] <= REPLY_HORIZON)
    return {"time":frame["time"], "tick":frame["tick"], "action":action, "reason":reason, "own":own,
            "support":support, "coordinated":coordinated,
            "creep":creep, "reply":reply, "caution":caution, "margin_pp":round(margin,1) if margin is not None else None,
            "outgoing_pct":round(outgoing,1) if own else None,"incoming_pct":round(incoming,1) if incoming is not None else None}


def select_decisions(cards, limit=5):
    groups = {k:[] for k in ("both","trade","creep","uncertain")}
    def priority(c):
        target = c["creep"]
        return (target is not None and c["own"] is not None,
                target is not None and target["alive_after_estimate"], c["own"] is not None and c["own"]["ready"] is True,
                c["reply"]["best"] is not None or bool(c["reply"]["controls"] or c["reply"]["blocked"]),
                -len(c["caution"]), abs(c["margin_pp"] or 0), -c["time"])
    for card in cards:
        groups[card["action"]].append(card)
    chosen = []
    for candidates in groups.values():
        candidates.sort(key=priority, reverse=True)
    def first_new(candidates):
        return next((c for c in candidates if all(abs(c["time"]-s["time"]) >= 15 and
                    (not c["creep"] or not s["creep"] or c["creep"]["id"] != s["creep"]["id"])
                    for s in chosen)), None)
    pairs = [c for candidates in groups.values() for c in candidates
             if ((c.get("support") or {}).get("movement") or {}).get("closing")]
    if pairs and limit > 0:
        chosen.append(max(pairs, key=lambda c:(c.get("coordinated", False), STATE_PRIORITY[c["support"]["state"]],
                                               priority(c))))
    for action, candidates in groups.items():
        if len(chosen) >= limit or any(c["action"] == action for c in chosen):
            continue
        top = first_new(candidates)
        if top is not None:
            chosen.append(top)
    while len(chosen) < limit:
        extra = first_new(sorted([*groups["both"],*groups["trade"],*groups["creep"]],key=priority,reverse=True))
        if extra is None:
            break
        chosen.append(extra)
    return sorted(chosen, key=lambda c:c["time"])


def _damage(action):
    lo, hi = action["damage_min"], action["damage_max"]
    return str(lo) if lo == hi else f"{lo}–{hi}"


def render_exchange_decisions(cards, enemy):
    parts = ['<div id="mid-exchanges"><h3>Размен или дальний крип</h3>']
    if not cards:
        parts.append('<p>Для сравнения не хватило одного кадра с двумя видимыми друг другу мидерами и доступным действием.</p></div>')
        return "".join(parts)
    parts.append('<p>Сравниваются первые действия с текущих позиций и потеря в процентах максимального здоровья. '
                 'Для перевеса требуется запас более двух процентных пунктов. Если та же кнопка забирает дальнего крипа, '
                 'для выбора нелетального размена требуется запас более четырёх пунктов. Готовность чужих кнопок взята из повтора. '
                 'У игрока в матче могли быть только подсказки по предыдущим кастам. '
                 'Подход союзной позиции 4/5 и её направленный контроль проверяются отдельно. '
                 'Следующие удары, движение, предметы и урон волны могут изменить результат.</p>')
    labels = {"both":"Добить и задеть мидера", "trade":"Размен по первой кнопке в твою пользу",
              "creep":"Приоритет — дальний крип", "uncertain":"Перевес в размене неясен"}
    for c in cards:
        own, creep, reply = c["own"], c["creep"], c["reply"]
        title = labels[c["action"]]
        if c["action"] == "creep" and creep["ready"] is not True:
            title = "Если удар готов — добей дальнего крипа"
        parts.append(f'<article><h4>{_clock(c["time"])} · {title}</h4><p>{escape(c["reason"])}</p>')
        if own:
            parts.append(f'<p>По {escape(enemy)} · {escape(own["spell"])} на дистанции {own["distance"]}, '
                         f'расчётный урон {_damage(own)} ({c["outgoing_pct"]:g}% запаса здоровья).')
            if own["health_cost"]:
                parts.append(f' Расход собственного здоровья — {own["health_cost"]:g}.')
            if own["ready"] is not True:
                parts.append(' Момент начала следующего удара нужно проверить в повторе.')
            if own["note"]:
                parts.append(' ' + escape(own["note"]))
            parts.append('</p>')
        if creep:
            parts.append(f'<p>По дальнему крипу · {escape(creep["spell"])}. У крипа {creep["hp"]} HP, '
                         f'расчётный урон {_damage(creep)}.')
            if creep["alive_after_estimate"]:
                parts.append(' Крип дожил до расчётного времени попадания.')
            else:
                parts.append(' Успевал ли удар до смерти крипа, проверь по повтору.')
            parts.append('</p>')
        elif own:
            parts.append('<p>В этом кадре добивание дальнего крипа доступным действием не подтверждено.</p>')
        if reply["best"]:
            best = reply["best"]
            parts.append(f'<p>Возможный ответ {escape(enemy)} · {escape(best["spell"])} на дистанции {best["distance"]}, '
                         f'урон {_damage(best)}. ')
            if best["ready"] is not True:
                parts.append('Время следующей автоатаки требует проверки. ')
            if best["note"]:
                parts.append(escape(best["note"]) + ' ')
            parts.append('</p>')
        if c["margin_pp"] is not None and c["action"] == "trade":
            parts.append(f'<p>Потеря здоровья соперника по расчёту — {c["outgoing_pct"]:g}%, '
                         f'твоя с учётом цены навыка и первого ответа — {c["incoming_pct"]:g}%. '
                         f'Запас для этого размена — {c["margin_pp"]:g} п. п.</p>')
        if reply["controls"]:
            parts.append('<p>Доступен контроль · ' + ', '.join(escape(x) for x in reply["controls"]) + '.</p>')
        if reply["blocked"]:
            labels_cd = [escape(b["spell"]) + (f' восстановится через {b["cooldown"]:.1f} с'
                         if "cooldown" in b else ' — не хватало маны') for b in reply["blocked"][:3]]
            parts.append('<p>В кадре повтора · ' + ', '.join(labels_cd) + '.</p>')
        if c["caution"]:
            parts.append('<p>Что проверить · ' + '. '.join(escape(x) for x in c["caution"][:4]) + '.</p>')
        parts.append(render_support(c, enemy))
        parts.append(render_prior_hits(c.get("prior_hits"),enemy))
        parts.append('</article>')
    return "".join([*parts,'</div>'])
