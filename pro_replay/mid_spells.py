"""Patch-specific mid spell catalogue and labelled single-impact estimates."""

from functools import lru_cache
from html import escape
import json
from math import isfinite
from pathlib import Path


@lru_cache(maxsize=1)
def spell_catalog():
    return json.loads(Path(__file__).with_name("mid_spellbook.json").read_bytes())


def hero_profile(hero_id):
    return spell_catalog()["heroes"].get(str(hero_id), {})


def hero_spells(hero_id):
    return hero_profile(hero_id).get("abilities", [])


def spell_specs(hero_id):
    return {spell["key"]: spell for spell in hero_spells(hero_id)}


def rank_value(values, rank):
    """Return a constant or the observed rank's value; reject invalid ranks."""
    if type(rank) is not int or rank < 1 or not isinstance(values, (tuple, list)) or not values:
        return None
    if len(values) == 1:
        return values[0]
    return values[rank - 1] if rank <= len(values) else None


def impact_damage(proof, rank, target):
    """Raw single impact, using the measured target pools for a missing-resource spell."""
    base = rank_value(proof.get("damage"), rank)
    pool = proof.get("missing_pool")
    if base is None or not pool:
        return base
    current, cap = target.get(pool), target.get("max_" + pool)
    if (not all(type(n) in (int, float) and isfinite(n) for n in (current, cap))
            or not 0 <= current <= cap or cap <= 0):
        return None
    return base * (cap - current)


def resolved_impact(spec, actor, rank):
    """Resolve a learned button's frame-specific conditions before either side's damage is priced."""
    proof = spec.get("proof")
    if not proof or type(rank) is not int or not 1 <= rank <= spec["max_rank"]:
        return None
    buttons = actor.get("buttons") or {}
    if proof.get("requires_unrooted"):
        state = actor.get("unit_state")
        if type(state) is not int or state < 0 or state & 1:
            return None
    for name in proof.get("inactive_buttons", ()):
        stop = buttons.get(name)
        if not isinstance(stop, dict) or stop.get("active") is not False:
            return None
    if proof.get("requires_charge"):
        button = buttons.get(spec["key"]) or {}
        if type(button.get("charges")) is not int or button["charges"] < 1:
            return None
    if proof.get("requires_off"):
        button = buttons.get(spec["key"]) or {}
        if button.get("toggled") is not False:
            return None
    scaling = proof.get("orb_scaling")
    if not scaling:
        return spec
    invoked = buttons.get(spec["key"]) or {}
    if (actor.get("hero_id") != 74 or invoked.get("active") is not True or invoked.get("slot") not in (3, 4)
            or invoked.get("kind") != "ability" or invoked.get("level") != rank):
        return None
    observed = dict(proof)
    for field, curve in scaling.items():
        orb = buttons.get(curve["orb"]) or {}
        if orb.get("kind") != "ability":
            return None
        value = rank_value(curve["values"], orb.get("level"))
        if value is None:
            return None
        observed[field] = [value]
    return {**spec, "proof": observed}


DAMAGE_NAMES = {"physical": "физический", "magical": "магический", "pure": "чистый"}
MECHANICS = {
    "direct": "Одно попадание", "tick": "Периодический урон", "attack": "Атака или усиление удара",
    "conditional": "Урон при выполнении условий", "summon": "Призванные юниты", "passive": "Пассивный эффект",
    "utility": "Контроль, защита или перемещение", "manual": "Механика для проверки в повторе",
    "orb": "Зависимость от сфер", "innate": "Врождённая способность",
}


def _numbers(series):
    return " / ".join(f"{v:g}".replace(".", ",") for v in series)


def render_spellbook(hero_id, *, learned=None):
    """Render a hero's sourced kit. Call only after validating the match patch."""
    profile = hero_profile(hero_id)
    if not profile:
        return ""
    h = escape(profile["name"])
    parts = [f'<details class="hero-spellbook"><summary>Способности и скачки силы · {h}</summary>',
             f'<p>{escape(profile["focus"])}</p>']
    for spell in profile["abilities"]:
        proof = spell["proof"]
        name = escape(spell["name"])
        kind = MECHANICS[spell["mechanic"]]
        parts.append(f"<details><summary>{name} · {kind}</summary>")
        note = spell.get("note")
        if note:
            parts.append(f"<p>{escape(note)}</p>")
        if proof:
            curve = proof.get("orb_scaling", {}).get("damage")
            series = curve["values"] if curve else proof["damage"]
            by_rank = "по уровням " + curve["orb"].removeprefix("invoker_").title() if curve else "по рангам"
            unit = {"mana": "за единицу недостающей маны", "hp": "за единицу недостающего здоровья"}.get(
                proof.get("missing_pool"), "одного попадания")
            parts.append(f"<p>Базовый {DAMAGE_NAMES[proof['damage_type']]} урон {unit} {by_rank} — {_numbers(series)}.</p>")
            if proof.get("instruction"):
                parts.append(f"<p>{escape(proof['instruction'])}</p>")
        if spell["rank_gates"]:
            parts.append(f"<p>Ранги доступны с уровней героя {_numbers(spell['rank_gates'])}. "
                         "Момент прокачки зависит от потраченных очков.</p>")
        if spell["parameters"]:
            parts.append("<ul>")
            for field in spell["parameters"]:
                parts.append(f"<li>{escape(field['label'])} — {_numbers(field['values'])}</li>")
            parts.append("</ul>")
        details = []
        for label, values in (("Мана", spell["mana"]), ("Перезарядка, с", spell["cooldown"]),
                              ("Расход здоровья", spell["health_cost"])):
            if values and any(values):
                details.append(label + " — " + _numbers(values))
        if details:
            parts.append("<p>" + ". ".join(details) + ".</p>")
        rank = learned.get(spell["key"]) if learned else None
        if type(rank) is int and 0 <= rank <= spell["max_rank"]:
            parts.append(f"<p>Ранг в выбранном кадре — {rank}.</p>")
        parts.append("</details>")
    if "game_source" in profile:
        parts.append(f'<p><a href="{escape(profile["game_source"], quote=True)}">Три дистанции Shadowraze в файле игры</a></p>')
    return "".join(parts) + f'<p><a href="https://www.dota2.com/datafeed/herodata?language=russian&amp;hero_id={int(hero_id)}">Параметры Valve</a></p></details>'
