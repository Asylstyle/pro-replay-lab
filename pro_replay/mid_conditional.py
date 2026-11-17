"""One attack or native attack-based ability on the 7.41f lane.

Values are for a successful hit against attribute-adjusted base defense. The
replay does not transmit the next normal attack order, evasion roll or barrier.
"""

from functools import lru_cache
from math import atan2, degrees, floor

from .mid_spells import rank_value, spell_catalog, spell_specs
from .replay_verification import DISABLED, _bits, _control, _distance, _num, _ready


# Magnitudes and range come from the archived Valve card, in the actual rank.
_ABILITY = {
    6: "drow_ranger_frost_arrows",
    35: "sniper_take_aim",
    44: "phantom_assassin_stifling_dagger",
    46: "templar_assassin_meld",
    47: "viper_poison_attack",
    56: "clinkz_searing_arrows",
    59: "huskar_burning_spear",
    75: "silencer_glaives_of_wisdom",
    76: "obsidian_destroyer_arcane_orb",
    100: "tusk_walrus_punch",
    106: "ember_spirit_sleight_of_fist",
    114: "monkey_king_boundless_strike",
    123: "hoodwink_acorn_shot",
    129: "mars_gods_rebuke",
}
_SPELL_ATTACK = {44, 106, 114, 123, 129}
_SOURCE_PATCH = "7.41f"


def conditional_keys(hero_id):
    key = _ABILITY.get(hero_id)
    return (key,) if key and spell_catalog()["patch"] == _SOURCE_PATCH else ()


def after_defense(raw_damage, kind, target):
    """Single damage packet, after base defense and hero attributes (no buffs)."""
    if not _num(raw_damage) or raw_damage < 0 or not isinstance(target, dict):
        return None
    hero = type(target.get("hero_id")) is int and target["hero_id"] > 0
    if kind == "physical":
        armor = target.get("armor")
        agility = target.get("agility_total") if hero else 0
        if not _num(armor) or not _num(agility) or agility < 0:
            return None
        armor += agility / 6
        multiplier = 1 - 0.06 * armor / (1 + 0.06 * abs(armor))
    elif kind == "magical":
        resistance = target.get("magic_resist_pct")
        intellect = target.get("intellect_total") if hero else 0
        if not _num(resistance) or not -100 <= resistance <= 100 or not _num(intellect) or intellect < 0:
            return None
        multiplier = max(0, 1 - (resistance + 0.1 * intellect) / 100)
    elif kind == "pure":
        multiplier = 1
    else:
        return None
    return floor(raw_damage * multiplier)


@lru_cache(maxsize=len(_ABILITY))
def _kit(hero_id):
    if not conditional_keys(hero_id):
        return None, {}
    spec = spell_specs(hero_id).get(_ABILITY[hero_id])
    if not spec:
        return None, {}
    return spec, {v["field"]: v["values"] for v in spec["parameters"]}


def _target_available(target, target_is_hero):
    if not isinstance(target, dict) or target.get("takes_damage") is not True:
        return False
    if not _num(target.get("hp")) or target["hp"] <= 0:
        return False
    if target_is_hero:
        if target.get("alive") is not True or type(target.get("hero_id")) is not int:
            return False
    elif target.get("name") not in ("npc_dota_creep_goodguys_ranged", "npc_dota_creep_badguys_ranged"):
        return False
    return _bits(target, (2, 8, 14, 33, 37, 39)) is False


def _option(target, raw_min, raw_max, *, reach, distance, name="Обычная атака", key="attack",
            cost=0, health_cost=0, bonus=0, ready=None, arrival=None, note="", partial=False):
    if not _num(reach) or reach <= 0 or distance > reach:
        return None
    damage_min = after_defense(raw_min, "physical", target)
    damage_max = after_defense(raw_max, "physical", target)
    if damage_min is None or damage_max is None:
        return None
    return {"name": name, "key": key, "damage_min": damage_min + bonus, "damage_max": damage_max + bonus,
            "ready": ready, "distance": round(distance), "cost": cost, "health_cost": health_cost,
            "arrival": arrival, "note": note, "partial": partial}


def _ability_option(actor, target, hero_id, target_is_hero, distance, lo, hi, clean_range):
    spec, fields = _kit(hero_id)
    if spec is None:
        return None
    key = spec["key"]
    button = actor["buttons"].get(key)
    if (not isinstance(button, dict) or button.get("kind") != "ability" or type(button.get("level")) is not int
            or not 1 <= button["level"] <= spec["max_rank"] or _control(actor, False, True)["state"] != "yes"
            or _bits(target, (9, 56)) is not False or any(c["state"] != "yes" for c in _ready(actor, button, False))):
        return None
    rank = button["level"]
    def value(field):
        return rank_value(fields.get(field), rank)

    cost, health_cost, bonus, note, arrival = button["mana_cost"], 0, 0, "", None
    name = spec["name"]
    reach = actor.get("attack_range")
    forced = hero_id in _SPELL_ATTACK
    if forced:
        if not clean_range:
            return None
        reach = rank_value(spec["range"], rank)
    if hero_id in (6, 56):
        flat = value("damage" if hero_id == 6 else "damage_bonus")
        if flat is None:
            return None
        lo, hi = lo + flat, hi + flat
    elif hero_id == 35:
        passive = actor["buttons"].get("sniper_headshot") or {}
        passive_rank = passive.get("level")
        if (passive.get("kind") != "ability" or type(passive_rank) is not int or not 1 <= passive_rank <= 4
                or value("headshot_chance") != 100 or _bits(actor, (30,)) is not False):
            return None
        passive_spec = spell_specs(35)["sniper_headshot"]
        damages = next((p["values"] for p in passive_spec["parameters"] if p["field"] == "damage"), [])
        flat = rank_value(damages, passive_rank)
        if flat is None:
            return None
        lo, hi = lo + flat, hi + flat
        name += " → Headshot"
        note = "После Take Aim нужна отдельная атака в течение эффекта. Sniper замедляется, обзор ограничен передним сектором."
    elif hero_id == 46:
        flat = value("bonus_damage")
        if flat is None or _bits(actor, (7,)) is not False:
            return None
        lo, hi = lo + flat, hi + flat
        name += " → атака"
        note = "После Meld атакуй без шага между кнопками. Расчёт использует броню цели до снижения от Meld."
    elif hero_id == 75:
        pct, intellect = value("intellect_damage_pct"), actor.get("intellect_total")
        if pct is None or not _num(intellect) or intellect < 0:
            return None
        bonus = after_defense(intellect * pct / 100, "magical", target)
    elif hero_id == 76:
        pct, spend = value("mana_pool_damage_pct"), value("mana_cost_percentage")
        if pct is None or spend is None:
            return None
        # This variable-cost network field can lag a tick/previous attack.
        # Keep the larger of the observed spend and Valve's current-mana price.
        cost = max(cost, actor["mana"] * spend / 100)
        if actor["mana"] < cost:
            return None
        bonus = after_defense((actor["mana"] - cost) * pct / 100, "pure", target)
    elif hero_id in (44, 123):
        flat = value("base_damage" if hero_id == 44 else "acorn_shot_damage")
        pct = value("attack_factor_tooltip" if hero_id == 44 else "base_damage_pct")
        if flat is None or pct is None:
            return None
        lo, hi = flat + lo * pct / 100, flat + hi * pct / 100
        # Valve 7.41f dagger_speed and Acorn projectile_speed, excluding turn time.
        speed = 1200 if hero_id == 44 else 2200
        arrival = rank_value(spec["cast_point"], rank) + distance / speed
        if hero_id == 123:
            name += " → по юниту"
            note = "Выбери режим прямого удара по юниту. При создании дерева траектория другая. Учтено первое попадание."
    elif hero_id == 106:
        # A point on the target itself is reachable. Outer AoE margin is omitted.
        # One attack per marked unit; another unit can be attacked first.
        if _bits(actor, (0, 45)) is not False:
            return None
        flat = value("bonus_hero_damage") if target_is_hero else 0
        if flat is None:
            return None
        lo, hi = lo + flat, hi + flat
        note = "Порядок ударов по целям в круге влияет на момент попадания."
    elif hero_id in (100, 114, 129):
        if hero_id == 100:
            flat, critical = value("bonus_damage"), value("crit_multiplier")
        elif hero_id == 114:
            flat, critical = value("strike_flat_damage"), value("strike_crit_mult")
            arrival = rank_value(spec["cast_point"], rank)
        else:
            flat = value("bonus_damage_vs_heroes") if target_is_hero else 0
            critical, reach = value("crit_mult"), value("radius")
            yaw = actor.get("facing_yaw")
            if not _num(yaw) or not 0 <= yaw < 360:
                return None
            heading = degrees(atan2(target["y"] - actor["y"], target["x"] - actor["x"]))
            # 140° cone in Valve's 7.41f profile; exclude a margin at the edges.
            if abs((heading - yaw + 180) % 360 - 180) > 65:
                return None
            arrival = rank_value(spec["cast_point"], rank)
        if flat is None or critical is None:
            return None
        # Bound both orders of applying the flat bonus and the critical strike.
        lo, hi = lo * critical / 100 + flat, (hi + flat) * critical / 100
    elif hero_id in (47, 59):
        if not target_is_hero:
            return None
        if hero_id == 59:
            percent = value("max_health_cost")
            if percent is None or not _num(actor.get("max_hp")) or not _num(actor.get("hp")):
                return None
            health_cost = actor["max_hp"] * percent / 100
            if actor["hp"] <= health_cost:
                return None
        dot = value("burn_damage" if hero_id == 59 else "damage")
        delayed = after_defense(dot, "magical", target)
        if delayed is None:
            return None
        note = (f"Один новый стак даёт расчётно ещё {delayed} периодического урона в секунду. "
                "В сравнении учитывается только первый удар. Продолжительность зависит от действий соперника.")
    if bonus is None:
        return None
    option = _option(target, lo, hi, reach=reach, distance=distance, name=name, key=key,
                     cost=cost, health_cost=health_cost, bonus=bonus, ready=True if forced and hero_id != 123 else None,
                     arrival=arrival, note=note, partial=hero_id in (47, 59, 123))
    if option:
        option["rank"] = rank
        if forced:
            option["cast_point"] = rank_value(spec["cast_point"], rank)
    return option


def attack_option(actor, target, hero_id, *, target_is_hero, include_orb=True, clean_range=True):
    """Best available single strike. Caller checks team, vision and the mid lane.

    A spell-generated strike has a ready *order*. Normal attack and Orb order
    timing is unknown. Individual procs, creeps' block, items and barriers can
    change damage on impact. HP and mana spent are returned separately.
    """
    if (not isinstance(actor, dict) or target is actor or actor.get("hero_id") != hero_id or actor.get("alive") is not True
            or not _target_available(target, target_is_hero) or not isinstance(actor.get("buttons"), dict)
            or _bits(actor, (*DISABLED, 1, 15, 32, 41)) is not False):
        return None
    if any(b.get("channeling") is True or b.get("in_phase") is True for b in actor["buttons"].values() if isinstance(b, dict)):
        return None
    distance = _distance(actor, target)
    lo, hi, plus = (actor.get(k) for k in ("attack_min", "attack_max", "attack_bonus"))
    if distance is None or not all(_num(v) for v in (lo, hi, plus)) or hi < lo or lo + plus <= 0:
        return None
    lo, hi = lo + plus, hi + plus
    normal = _option(target, lo, hi, reach=actor.get("attack_range"), distance=distance)
    if include_orb:
        candidate = _ability_option(actor, target, hero_id, target_is_hero, distance, lo, hi, clean_range)
        if candidate and (normal is None or candidate["damage_min"] >= normal["damage_min"]):
            return candidate
    return normal
