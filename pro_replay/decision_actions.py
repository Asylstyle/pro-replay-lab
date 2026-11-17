"""Find a specific button and recipient for a recorded combat decision.

The opportunity is a counterfactual. A cast counter gives an interval, even
when the outcome used to locate it in the replay has an exact timestamp.
"""

from .analysis import select_player
from .build_review import SLOTS, _fight_counts, _fights
from .decision_events import inventory_evidence, item_present, known_dead_at, observed_events
from .fight_coach import CANDIDATES, FAMILIES, ITEM_USE
from .fight_review import PREFIX, _counts, _spell_name
from .report import _clock


# Internal ability names refer to the pinned OpenDota/Valve definitions used by
# fight_review. Native spells are offered only after a recorded use by this player.
CONTROL = set("""
lion_voodoo lion_impale shadow_shaman_voodoo shadow_shaman_shackles rubick_telekinesis
vengefulspirit_magic_missile sven_storm_bolt chaos_knight_chaos_bolt ogre_magi_fireblast
ogre_magi_unrefined_fireblast dragon_knight_dragon_tail bane_fiends_grip beastmaster_primal_roar
sandking_burrowstrike earthshaker_fissure magnataur_reverse_polarity tidehunter_ravage
centaur_hoof_stomp slardar_slithereen_crush axe_berserkers_call pudge_dismember
tiny_avalanche leshrac_split_earth lina_light_strike_array nyx_assassin_impale
mirana_arrow windrunner_shackleshot witch_doctor_paralyzing_cask mars_spear
hoodwink_bushwhack kunkka_torrent rattletrap_hookshot techies_suicide
skeleton_king_hellfire_blast tusk_walrus_punch
antimage_mana_void enigma_malefice spirit_breaker_nether_strike
ancient_apparition_cold_feet dark_willow_cursed_crown
""".split())
SILENCE = {"death_prophet_silence", "puck_waning_rift", "skywrath_mage_ancient_seal",
           "drow_ranger_wave_of_silence", "silencer_global_silence"}
CHANNEL = {"bane_fiends_grip", "shadow_shaman_shackles", "pudge_dismember", "enigma_black_hole",
           "witch_doctor_death_ward"}
PIERCES_IMMUNITY = {"axe_berserkers_call", "bane_fiends_grip", "beastmaster_primal_roar",
                    "magnataur_reverse_polarity", "pudge_dismember", "rattletrap_hookshot",
                    "silencer_global_silence", "spirit_breaker_nether_strike", "tusk_walrus_punch"}
CONDITIONS = {
    "mars_spear": "Для оглушения проверь траекторию копья и препятствие за целью.",
    "hoodwink_bushwhack": "Для срабатывания рядом с целью нужно дерево.",
    "ancient_apparition_cold_feet": "Оглушение срабатывает с задержкой, если цель останется в области.",
    "dark_willow_cursed_crown": "Учитывай задержку срабатывания оглушения.",
    "mirana_arrow": "Проверь время полёта и юнитов на линии стрелы.",
    "kunkka_torrent": "Проверь задержку подъёма и положение цели.",
    "antimage_mana_void": "Для урона важна недостающая мана цели.",
    "leshrac_split_earth": "Проверь задержку срабатывания и место цели.",
    "windrunner_shackleshot": "Проверь, была ли за целью подходящая привязка для длительного оглушения.",
    "slark_pounce": "Проверь направление и кто стоял первым на пути прыжка.",
    "earthshaker_echo_slam": "Проверь расстояние до героя и сколько юнитов было рядом.",
    "juggernaut_omni_slash": "Учитывай соседние цели, на которые могут уйти прыжки.",
    "juggernaut_swift_slash": "Учитывай соседние цели, на которые могут уйти прыжки.",
}
SAVES = {
    "dazzle_shallow_grave": "дать союзнику время пережить фокус под Shallow Grave",
    "abaddon_aphotic_shield": "заранее поглотить часть урона барьером",
    "oracle_false_promise": "отсрочить урон и дать команде время на лечение",
    "shadow_demon_disruption": "временно убрать союзника из-под фокуса, сохранив позицию для выхода",
    "omniknight_purification": "восстановить здоровье союзника до следующей атаки",
    "omniknight_guardian_angel": "прикрыть союзника от физических атак",
    "abaddon_death_coil": "восстановить здоровье союзника",
    "chen_hand_of_god": "восстановить здоровье союзной команды",
    "lich_frost_shield": "прикрыть союзника от последующих атак под Frost Shield",
    "legion_commander_press_the_attack": "попробовать развеять контроль с союзника и поддержать его восстановление",
    "winter_wyvern_cold_embrace": "выиграть время против физического урона, если союзнику не угрожает магический залп",
}
SELF_DEFENSE = {
    "ursa_enrage": "пережить опасный отрезок под снижением входящего урона",
    "troll_warlord_battle_trance": "пережить смертельный урон во время Battle Trance",
    "weaver_time_lapse": "вернуть более ранние здоровье и позицию, если они были выгоднее",
    "phoenix_supernova": "проверить возможность пережить фокус в Supernova, если яйцо можно защитить",
    "slark_shadow_dance": "ограничить возможности противника атаковать тебя в Shadow Dance",
    "juggernaut_blade_fury": "снизить угрозу магического урона и обычного контроля до входа",
}
# These abilities deal damage to a chosen hero or the area where they stand.
# Require recorded infliction of hero damage in this match as well as spell
# use in this or an earlier fight. Do not use generic click-target assumptions
# for movement, healing, passives, toggle abilities or random bounces.
OFFENSE = set("""
lina_dragon_slave lina_laguna_blade lion_finger_of_death queenofpain_shadow_strike queenofpain_sonic_wave
leshrac_lightning_storm zuus_lightning_bolt zuus_arc_lightning tinker_laser tinker_march_of_the_machines
rubick_fade_bolt skywrath_mage_arcane_bolt skywrath_mage_mystic_flare lich_frost_nova lich_chain_frost
crystal_maiden_crystal_nova death_prophet_carrion_swarm death_prophet_spirit_siphon pugna_nether_blast
vengefulspirit_wave_of_terror witch_doctor_maledict bane_brain_sap shadow_shaman_ether_shock
jakiro_dual_breath jakiro_macropyre earthshaker_echo_slam
invoker_chaos_meteor invoker_deafening_blast invoker_sun_strike invoker_emp
nevermore_shadowraze1 nevermore_shadowraze2 nevermore_shadowraze3 phantom_assassin_stifling_dagger
juggernaut_omni_slash juggernaut_swift_slash troll_warlord_whirling_axes_ranged
troll_warlord_whirling_axes_melee mars_gods_rebuke morphling_waveform morphling_adaptive_strike_agi
sniper_shrapnel sniper_assassinate slark_dark_pact slark_pounce gyrocopter_call_down
razor_plasma_field sandking_epicenter phoenix_fire_spirits phoenix_sun_ray
ember_spirit_sleight_of_fist kez_echo_slash
void_spirit_aether_remnant void_spirit_astral_step puck_illusory_orb pangolier_swashbuckle
rattletrap_rocket_flare shredder_whirling_death shredder_chakram snapfire_scatterblast snapfire_mortimer_kisses
shadow_demon_shadow_poison shadow_demon_demonic_purge omniknight_hammer_of_purity
venomancer_venomous_gale venomancer_noxious_plague drow_ranger_multishot hoodwink_sharpshooter
spectre_spectral_dagger phantom_lancer_spirit_lance monkey_king_boundless_strike
earth_spirit_boulder_smash dawnbreaker_celestial_hammer dawnbreaker_fire_wreath
luna_lucent_beam mirana_starfall terrorblade_reflection sven_storm_bolt
""".split())
ACTIVE_ITEMS = {"sheepstick", "abyssal_blade", "black_king_bar", "glimmer_cape", "manta",
                "cyclone", "pipe", "force_staff", "ghost"}


def _targets(actor, key):
    targets = actor.get("ability_targets")
    return _counts(targets.get(key)) if isinstance(targets, dict) else None


def _option(kind, event, target, enemy, button, action, why, evidence, check, strength, me):
    slot = target["slot"]
    return {"kind": kind, "target": target["hero"], "target_slot": slot, "enemy_focus": enemy["hero"],
            "focus_slot": enemy["slot"], "button": button, "action": action, "why": why,
            "evidence": evidence, "check": check, "strength": strength,
            "recipient_side": "self" if slot == me else ("ally" if slot // 128 == me // 128 else "enemy"),
            "event_time": event["time"], "event_kind": event["kind"], "event_actor_slot": event.get("actor_slot"),
            "event_target_slot": event.get("target_slot")}


def _known_buttons(raw, slot, start, unit):
    prior = {}
    for window in _fights(raw):
        if window["end"] >= start:
            continue
        for key, n in (_fight_counts(window, slot, "ability_uses") or {}).items():
            if n > 0 and key.startswith(unit.removeprefix(PREFIX) + "_"):
                prior[key] = max(prior.get(key, -1), window["end"])
    return prior


def _cast_evidence(actor, casts, key, target, by_unit, earlier):
    """Also allow reviewing the target of an observed use when attribution is missing."""
    if casts is None:
        return None
    name = _spell_name(key, "")
    if not casts.get(key):
        if key not in earlier:
            return None
        return f"{name} применялся в более ранней драке (до {_clock(earlier[key])}). В журнале этого боя применений — 0.", True
    counter = _targets(actor, key)
    if counter and counter.get(target["unit"], 0) > 0:
        return None
    if not counter or sum(counter.values()) != casts[key] or any(u not in by_unit for u in counter):
        return f"{name} применялся в этой драке ×{casts[key]}. По журналу нельзя восстановить его цель для каждого применения.", False
    other = ", ".join(by_unit[u]["hero"] for u in sorted(counter) if counter[u] > 0)
    return f"Все отмеченные применения {name} за интервал пришлись на другие цели ({other}).", True


def _threats_for(fight, window, event, victim, opponents, roster, me):
    """Prefer direct evidence; a cast elsewhere in the interval carries lower confidence."""
    direct = event.get("actor_slot")
    options = []
    for t in fight.get("threats", []):
        matches = [p for p in opponents if p["id"] == t["hero_id"]]
        if len(matches) != 1:
            continue
        enemy = matches[0]
        part = window["players"][SLOTS.index(enemy["slot"])]
        targets = _targets(part, t["key"]) if isinstance(part, dict) and t["source"] == "ability_uses" else None
        on_victim = (targets or {}).get(victim["unit"], 0)
        linked = bool(on_victim or (enemy["slot"] == direct and event["kind"] == "hero_hit" and
                                    event.get("ability") == t["key"]))
        if not linked and direct != enemy["slot"] and victim["slot"] != me:
            # A separate ally death is not proof that a caster elsewhere in the
            # fight threatened that ally.
            continue
        evidence = f'{enemy["hero"]} применял {t["ability"]} за интервал ×{t["casts"]}.'
        if on_victim:
            evidence += f' Целью отмечен {victim["hero"]} ×{on_victim}.'
        options.append((enemy, t, linked, evidence))
    if direct in {p["slot"] for p in opponents} and not any(e["slot"] == direct for e, *_ in options):
        enemy = roster[direct]
        options.append((enemy, None, True, event["text"]))
    return options


def _skill_options(event, victim, enemy, threat, linked, evidence, ctx):
    options = []
    me, slot, casts, own = ctx["me"], ctx["me"]["slot"], ctx["casts"], ctx["actor"]
    self_hit = victim["slot"] == slot
    key_threat = threat["key"] if threat else None
    channel = key_threat in CHANNEL
    spell = threat["ability"] if threat else ""
    for key in sorted(ctx["eligible"]):
        button = _spell_name(key, "")
        if key in CONTROL or key in SILENCE:
            target = enemy
            kind = "interrupt" if channel else "focus"
            action = (f"Прервать {spell} у {enemy['hero']} с помощью {button}, если успевал выйти на дистанцию."
                      if channel else f"Начать контроль с {button} по {enemy['hero']}, если герой был виден и доступен.")
            why = (f"Прерывание канала {spell} сократило бы время его действия." if channel else
                   f"Контроль дал бы {victim['hero']} время разорвать дистанцию и союзникам — переключиться на {enemy['hero']}.")
            if not channel and key in SILENCE:
                if not threat or threat["source"] != "ability_uses":
                    continue
                why = f"Безмолвие до применения {spell} могло отсрочить следующий каст {enemy['hero']}."
            if key == "silencer_global_silence":
                action = f"Включить Global Silence, когда {enemy['hero']} готовился применить {spell or 'заклинание'}."
        elif key in SAVES and not self_hit:
            if key in ("omniknight_guardian_angel", "lich_frost_shield", "winter_wyvern_cold_embrace") and (not threat or threat["category"] != "physical_pressure"):
                continue
            target, kind = victim, "protect"
            action = (f"Прикрыть {victim['hero']} с помощью {button} до следующего попадания."
                      if key in ("chen_hand_of_god", "omniknight_guardian_angel") else
                      f"Применить {button} на {victim['hero']} до следующего попадания.")
            why = f"Смысл решения — {SAVES[key]}."
        elif key in SELF_DEFENSE and self_hit and event["kind"] == "death":
            if key == "juggernaut_blade_fury" and (not threat or threat["category"] not in ("magic_burst", "silence", "target")):
                continue
            target, kind = me, "self_defense"
            action = f"Включить {button} до опасного входа, если способность была готова."
            why = f"Это дало бы возможность {SELF_DEFENSE[key]}."
        elif key in OFFENSE and ctx["inflicted"].get(key, 0) and (linked or event.get("actor_slot") == enemy["slot"]):
            target, kind = enemy, "focus"
            action = f"Сосредоточить урон {button} на {enemy['hero']}, если цель была доступна и союзники могли продолжить атаку."
            why = (f"{enemy['hero']} применял {spell}. Давление на этого героя могло ограничить его следующий выход на цель."
                   if threat else f"Журнал связывает {enemy['hero']} с уроном по {victim['hero']}. "
                                  "Сверь, могла ли команда быстро вывести этого противника из драки.")
        else:
            continue
        if (key, target["slot"]) in ctx["hits"] or (target["slot"] == slot and (casts or {}).get(key, 0)):
            continue
        cast = _cast_evidence(own, casts, key, target, ctx["units"], ctx["prior"])
        if not cast:
            continue
        use, gap = cast
        if not gap:
            # A self-cast already recorded, or damage already dealt to the same
            # hero, gives no evidence for a different focus.
            if not (linked or channel or enemy["slot"] == event.get("actor_slot")):
                continue
        check = "Сверь обзор, расстояние, ману и перезарядку перед событием."
        if channel and kind == "interrupt":
            check += f" Успевал ли {me['hero']} выйти из-под контроля и достать {enemy['hero']}, пока канал ещё шёл?"
        if (key in CONTROL or key in SILENCE) and key not in PIERCES_IMMUNITY:
            check += " Проверь дебафф-иммунитет цели."
        if key in CONDITIONS:
            check += " " + CONDITIONS[key]
        if not gap:
            check += f" Уточни, кто уже попал под {button}. Если это был {target['hero']}, проверь только момент применения."
        strength = 3 if linked or channel and enemy["slot"] == event.get("actor_slot") else 2
        if threat and not linked and enemy["slot"] != event.get("actor_slot"):
            strength = 1
        if not gap or key in OFFENSE and key not in CONTROL:
            strength = min(strength, 1 if not gap else 2)
        options.append(_option(kind, event, target, enemy, button, action, why, evidence + " " + use,
                               check, strength, slot))
    return options


def _item_options(event, victim, enemy, threat, linked, evidence, fight, ctx):
    if not threat or ctx["item_uses"] is None:
        return []
    result = []
    for item in fight.get("items", ()):
        key = item["key"]
        if (key not in ACTIVE_ITEMS or item["status"] != "prior_purchase" or
                key not in CANDIDATES.get(threat["category"], ())):
            continue
        family = FAMILIES.get(key, (key,))
        if any(ctx["item_uses"].get(k, 0) for k in family):
            continue
        me = ctx["me"]
        if item_present(ctx["raw"], me["slot"], key, event["time"]) is False:
            continue
        if key in ("sheepstick", "abyssal_blade"):
            target, kind = enemy, "focus"
        elif key in ("force_staff", "glimmer_cape", "pipe"):
            target = victim
            kind = "self_defense" if target["slot"] == me["slot"] else "protect"
            if kind == "protect" and not linked and enemy["slot"] != event.get("actor_slot"):
                continue
        elif victim["slot"] == me["slot"]:
            target, kind = me, "self_defense"
        else:
            continue
        name = item["name"]
        # Only recommend the button that was actually bought, not an inferred
        # base item whose active differs from that of an upgrade.
        if len(family) > 1:
            purchases = [(p["time"], p["key"]) for p in ctx["purchase_log"]
                         if p.get("key") in family and type(p.get("time")) is int and p["time"] < event["time"]]
            if not purchases or max(purchases)[1] != key or min(t for t, _ in purchases) >= fight["start"]:
                continue
        if key == "black_king_bar":
            action = f"Включить {name} до входа в радиус {threat['ability']}."
        elif key == "pipe":
            action = f"Активировать {name}, пока {target['hero']} в радиусе барьера."
        elif key == "manta" or key == "cyclone":
            action = f"Снять развеиваемое безмолвие с себя кнопкой {name}, если оно попало по тебе."
        elif key == "ghost":
            action = f"Включить {name} до серии физических атак."
        else:
            action = f"Применить {name} на {target['hero']}, если цель доступна."
        # The shopping report shows each item once, even when two opponents
        # presented separate threats that the same active could answer.
        why = ITEM_USE[key]
        if key == "glimmer_cape" and threat["category"] == "target":
            why = "Если противнику нужна видимая цель, ранний Glimmer может скрыть её. Уже наложенный контроль сохранится."
        check = f"Проверь доставку, активный слот и перезарядку {name}, обзор и расстояние до выбранного героя."
        strength = 3 if linked else 2 if enemy["slot"] == event.get("actor_slot") else 1
        inventory = inventory_evidence(ctx["raw"], me["slot"], key, event["time"])
        facts = evidence + " " + item["evidence"] + (" " + inventory if inventory else "")
        result.append(_option(kind, event, target, enemy, name, action, why,
                              facts, check, strength, me["slot"]))
    return result


def decision_options(raw, slot, heroes, fight):
    """Return conditional decisions grounded in this player's own recorded resources."""
    player = select_player(raw, None, slot)
    if not player or slot not in SLOTS:
        return []
    window = next((f for f in _fights(raw) if (f["start"], f["end"]) == (fight["start"], fight["end"])), None)
    if not window:
        return []
    roster = {}
    for p in raw.get("players", []):
        if not isinstance(p, dict) or p.get("player_slot") not in SLOTS:
            continue
        s, hid = p["player_slot"], p.get("hero_id")
        hero = heroes.get(hid, {})
        if not hero.get("name") or not hero.get("localized_name"):
            continue
        if s in roster:
            return []
        roster[s] = {"unit": hero["name"], "hero": hero["localized_name"], "id": hid, "slot": s}
    if slot not in roster or len({p["unit"] for p in roster.values()}) != len(roster):
        return []
    me = roster[slot]
    actor = window["players"][SLOTS.index(slot)]
    if not isinstance(actor, dict):
        return []
    prior = _known_buttons(raw, slot, fight["start"], me["unit"])
    casts = _fight_counts(window, slot, "ability_uses")
    prefix = me["unit"].removeprefix(PREFIX) + "_"
    ctx = {"raw": raw, "me": me, "prior": prior, "casts": casts, "actor": actor,
           "inflicted": _counts(player.get("damage_inflictor")) or {},
           "eligible": prior.keys() | {k for k in (casts or {}) if k.startswith(prefix)},
           "units": {p["unit"]: p for p in roster.values()},
           "item_uses": _fight_counts(window, slot, "item_uses"),
           "purchase_log": [p for p in player.get("purchase_log") or [] if isinstance(p, dict)]}
    events = fight.get("observed_events")
    if events is None:
        events = observed_events(raw, slot, heroes, fight)
    ctx["hits"] = {(e.get("ability"), e.get("target_slot")) for e in events
                   if e["kind"] == "hero_hit" and e.get("actor_slot") == slot}
    opponents = [p for p in roster.values() if p["slot"] // 128 != slot // 128]
    result = []
    life_checks = {}
    def dead(target_slot, time):
        key = (target_slot, time)
        if key not in life_checks:
            life_checks[key] = known_dead_at(raw, target_slot, heroes, time, before=True)
        return life_checks[key]
    for event in events:
        victim = roster.get(event.get("target_slot"))
        if not victim or victim["slot"] // 128 != slot // 128 or event["kind"] not in ("death", "hero_hit"):
            continue
        time = event["time"]
        if dead(slot, time) or dead(victim["slot"], time):
            continue
        for enemy, threat, linked, proof in _threats_for(fight, window, event, victim, opponents, roster, slot):
            if dead(enemy["slot"], time) or enemy["slot"] == slot:
                continue
            proof = f"{event['text'].rstrip('.')}. {proof}" if event["text"] != proof else proof
            result.extend(_skill_options(event, victim, enemy, threat, linked, proof, ctx))
            result.extend(_item_options(event, victim, enemy, threat, linked, proof, fight, ctx))
    order = ("protect", "interrupt", "self_defense", "focus") if fight.get("position") in (4, 5) else (
        "interrupt", "self_defense", "focus", "protect")
    distinct = {}
    for option in sorted(result, key=lambda o: (-o["strength"], order.index(o["kind"]), o["event_time"], o["button"])):
        key = tuple(option[k] for k in ("event_time", "event_kind", "event_actor_slot", "event_target_slot",
                                        "focus_slot", "target_slot", "button"))
        distinct.setdefault(key, option)
    return list(distinct.values())
