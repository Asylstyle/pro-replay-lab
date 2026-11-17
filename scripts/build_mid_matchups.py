"""Freeze Valve's base attack statistics and mechanically reviewed lane spells."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pro_replay.reference_policy import make_policy
from pro_replay.source import valve_patches

PATCH = "7.41f"
URL = "https://www.dota2.com/datafeed/"
OUTPUT = ROOT / "pro_replay/mid_matchup_data.json"

# Each selected value is one named component of a basic spell against a unit.
# Keep explicit conditions here; a tick, an impact and a full combo differ.
NUKES = {
    "earthshaker_fissure": ("fissure_damage", "Попадание линией Fissure может одновременно добить крипа и перекрыть проход."),
    "nevermore_shadowraze1": ("shadowraze_damage", "Базовый урон ближнего койла. Дистанция, накопленные попадания и души меняют результат."),
    "puck_illusory_orb": ("damage", "Урон при попадании сферой. Дополнительный периодический урон здесь опущен. Проверь траекторию через крипа."),
    "storm_spirit_static_remnant": ("static_remnant_damage", "Взрыв ремнанта требует срабатывания рядом с целью. Overload считается отдельно."),
    "zuus_arc_lightning": ("arc_damage", "Базовый урон одного попадания цепи. Отскок через крипа может достать героя и пропушить волну."),
    "lina_dragon_slave": ("dragon_slave_damage", "Урон из описания Dragon Slave. У Lina есть отдельное горение Slow Burn, поэтому итоговый урон приходит частями."),
    "tinker_laser": ("laser_damage", "Laser наносит чистый урон одной цели. Ослепление соперника также меняет следующий размен автоатаками."),
    "necrolyte_death_pulse": (None, "Урон одного импульса. Проверь, попадает ли цель в радиус и подлечится ли при этом союзная волна."),
    "queenofpain_scream_of_pain": ("damage", "Базовый урон крика по целям в радиусе. Перед подходом проверь, сколько ответных ударов получишь."),
    "death_prophet_carrion_swarm": ("damage", "Если герой стоит за крипами, направление стаи может дать одновременно добивание и размен."),
    "pugna_nether_blast": ("blast_damage", "Взрыв с задержкой. Для добивания оцени здоровье крипа на момент взрыва."),
    "leshrac_split_earth": (None, "Урон при попадании. Задержка Split Earth даёт сопернику время выйти из области."),
    "huskar_inner_fire": ("damage", "Урон ближней области и отталкивание. Учитывай стоимость здоровьем и положение пачки после нажатия."),
    "batrider_flamebreak": ("damage_impact", "Только начальное попадание. Отталкивание и последующее горение влияют на добивание отдельно."),
    "magnataur_shockwave": ("shock_damage", "Базовый урон попадания волной. Проверь, совпадает ли линия к герою с нужным крипом."),
    "shredder_whirling_death": ("whirling_damage", "Показан базовый чистый урон. Каждое срубленное дерево добавляет урон."),
    "earth_spirit_boulder_smash": ("rock_damage", "Значение для попадания снарядом. Убедись, что есть подходящая цель для удара и траектория."),
    "pangolier_shield_crash": ("damage", "Физический урон в области приземления. Броня цели меняет урон."),
    "void_spirit_dissimilate": (None, "Урон при выходе из портала. Промежуток до появления важен для добивания."),
    "void_spirit_resonant_pulse": ("damage", "Урон одного импульса. Проверь, зацепит ли он героя рядом с крипом для усиления щита."),
}

# Conventional first ultimate point at hero level 6; excludes unusual gates.
ULTIMATES = {7, 11, 13, 17, 19, 21, 22, 23, 25, 34, 35, 36, 39, 43, 45, 46, 52, 59, 65,
             76, 97, 98, 106, 107, 120, 126}

def fetch(endpoint):
    with urlopen(URL + endpoint, timeout=25) as response:
        return json.load(response)["result"]["data"]["heroes"]


def compact(hero):
    row = {k: hero[k] for k in ("name_loc", "primary_attr", "damage_min", "damage_max", "attack_range", "attack_rate")}
    row["attributes"] = [hero[key + "_base"] for key in ("str", "agi", "int")]
    row["gains"] = [hero[key + "_gain"] for key in ("str", "agi", "int")]
    row["nukes"] = []
    for ability in hero["abilities"]:
        field = ability["name"]
        if field == "void_spirit_intrinsic_edge":
            bonus = next(x for x in ability["special_values"] if x["name"] == "damage_stat_bonus_pct")
            row["universal_bonus_pct"] = bonus["values_float"][0]
        if (hero["id"] in ULTIMATES and ability["type"] == 1 and ability["max_level"] == 3
                and not ability["ability_is_granted_by_scepter"] and not ability["ability_is_granted_by_shard"]):
            row["ultimate"] = {"id": ability["id"], "name": ability["name_loc"]}
        if field not in NUKES:
            continue
        key, note = NUKES[field]
        damage = ability["damages"] if key is None else next(
            s["values_float"] for s in ability["special_values"] if s["name"] == key)
        if len(damage) != 4 or ability["max_level"] != 4 or ability["type"] != 0 or any(x <= 0 for x in damage):
            raise ValueError("Changed spell data: " + field)
        row["nukes"].append({"id": ability["id"], "name": ability["name_loc"], "damage": damage,
                             "damage_type": ability["damage"], "mana": ability["mana_costs"],
                             "cooldown": ability["cooldowns"], "note": note})
    return row


def main():
    policy = make_policy(valve_patches(), as_of=int(time.time()))
    if policy["patch"] != PATCH:
        raise ValueError("Review formulas, ability gates and chosen damage fields for the new patch first.")
    ids = sorted(hero["id"] for hero in fetch("herolist?language=english"))
    def fetch_hero(hero_id):
        hero, = fetch(f"herodata?language=english&hero_id={hero_id}")
        if hero["id"] != hero_id:
            raise ValueError("Unexpected hero identity in Valve feed.")
        return compact(hero)
    with ThreadPoolExecutor(max_workers=4) as pool:
        heroes = dict(zip(ids, pool.map(fetch_hero, ids)))
    end = make_policy(valve_patches(), as_of=int(time.time()))
    if end["patch"] != PATCH or sum(len(h["nukes"]) for h in heroes.values()) != len(NUKES):
        raise ValueError("The upstream roster or patch changed during collection.")
    data = {"patch": PATCH, "patch_start": end["patch_start"], "checked_through": end["as_of"], "heroes": heroes}
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    temp = OUTPUT.with_suffix(".tmp")
    temp.write_text(text, encoding="utf8")
    temp.replace(OUTPUT)


if __name__ == "__main__":
    main()
