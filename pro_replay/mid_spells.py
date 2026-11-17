"""Patch-specific mid spell catalogue and labelled single-impact estimates."""

from functools import lru_cache
from html import escape
import json
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
            parts.append(f"<p>Базовый {DAMAGE_NAMES[proof['damage_type']]} урон одного попадания по рангам — "
                         f"{_numbers(proof['damage'])}.</p>")
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
