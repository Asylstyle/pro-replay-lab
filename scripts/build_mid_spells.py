"""Build 7.41f spell cards from Valve's hero feed. Retain numbers, not guide text."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html import unescape
import json
from pathlib import Path
import re
import sys
from time import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pro_replay.reference_policy import make_policy
from pro_replay.source import valve_patches
from scripts.build_mid_matchups import PATCH, URL

SHADOWRAZE_GAME_FILE = (
    "https://github.com/SteamTracking/GameTracking-Dota2/blob/ab523510b09908cedec32e7b0cce6333c27e5af2/"
    "game/dota/pak01_dir/scripts/npc/heroes/npc_dota_hero_nevermore.txt#L301-L534"
)
# Valve's public hero feed groups the three linked buttons under the close one.
# The pinned game file above defines the same base damage, radius and cast point
# for all three. The IDs are also listed in odota/dotaconstants' ability_ids.json.
SHADOWRAZES = (
    ("nevermore_shadowraze1", 5059, "Shadowraze · ближний", 200),
    ("nevermore_shadowraze2", 5060, "Shadowraze · средний", 450),
    ("nevermore_shadowraze3", 5061, "Shadowraze · дальний", 700),
)

@dataclass(frozen=True)
class Impact:
    shape: str
    damage: str
    reach: str = "cast_ranges"
    speed: str | None = None
    delay: str | None = None
    target: str = "both"
    partial: bool = False


# Every listed value is the first independently deliverable damage component
# on the aimed target. Bounces, procs, attacks and full DoT totals are excluded.
IMPACTS = {
    "bane_brain_sap": Impact("unit", "brain_sap_damage"),
    "bloodseeker_blood_bath": Impact("point", "damage", delay="delay"),
    "crystal_maiden_crystal_nova": Impact("point", "nova_damage"),
    "earthshaker_fissure": Impact("line", "fissure_damage"),
    "morphling_waveform": Impact("line", "damages", speed="speed"),
    **{name: Impact("forward_circle", "shadowraze_damage", "shadowraze_radius") for name, *_ in SHADOWRAZES},
    "phantom_lancer_spirit_lance": Impact("unit", "lance_damage", speed="lance_speed"),
    "puck_illusory_orb": Impact("line", "damage", "max_distance", speed="orb_speed"),
    "puck_waning_rift": Impact("point", "damage", "max_distance"),
    "sandking_burrowstrike": Impact("line", "damages", speed="burrow_speed"),
    "storm_spirit_static_remnant": Impact("self_radius", "static_remnant_damage", "static_remnant_radius",
                                          delay="static_remnant_delay"),
    "sven_storm_bolt": Impact("unit", "damages", speed="bolt_speed"),
    "vengefulspirit_magic_missile": Impact("unit", "magic_missile_damage", speed="magic_missile_speed"),
    "vengefulspirit_wave_of_terror": Impact("line", "damage", speed="wave_speed"),
    "zuus_arc_lightning": Impact("unit", "arc_damage"),
    "zuus_lightning_bolt": Impact("unit", "damage"),
    "lina_dragon_slave": Impact("line", "dragon_slave_damage", speed="dragon_slave_speed"),
    "lina_light_strike_array": Impact("point", "light_strike_array_damage", delay="light_strike_array_delay_time"),
    "lina_laguna_blade": Impact("unit", "damage", delay="damage_delay"),
    "shadow_shaman_ether_shock": Impact("unit", "damage"),
    "slardar_slithereen_crush": Impact("self_radius", "crush_damage", "crush_radius"),
    "tidehunter_gush": Impact("unit", "gush_damage", speed="projectile_speed"),
    "tinker_laser": Impact("unit", "laser_damage"),
    "necrolyte_death_pulse": Impact("self_radius", "damages", "area_of_effect", speed="projectile_speed"),
    "beastmaster_primal_roar": Impact("unit", "damage"),
    "queenofpain_shadow_strike": Impact("unit", "strike_damage", speed="projectile_speed"),
    "queenofpain_scream_of_pain": Impact("self_radius", "damage", "area_of_effect", speed="projectile_speed"),
    "venomancer_venomous_gale": Impact("line", "strike_damage", speed="speed"),
    "skeleton_king_hellfire_blast": Impact("unit", "damage", speed="blast_speed"),
    "death_prophet_carrion_swarm": Impact("line", "damage", "range", speed="speed"),
    "pugna_nether_blast": Impact("point", "blast_damage", delay="delay"),
    "luna_lucent_beam": Impact("unit", "beam_damage"),
    "dragon_knight_breathe_fire": Impact("line", "damage", speed="speed"),
    "leshrac_split_earth": Impact("point", "damages", delay="delay"),
    "leshrac_lightning_storm": Impact("unit", "damage"),
    "dark_seer_vacuum": Impact("point", "damage", delay="duration"),
    "huskar_inner_fire": Impact("self_radius", "damage", "radius"),
    "night_stalker_void": Impact("unit", "damage"),
    "broodmother_spawn_spiderlings": Impact("unit", "damage", speed="projectile_speed"),
    "bounty_hunter_shuriken_toss": Impact("unit", "bonus_damage", speed="speed"),
    "brewmaster_thunder_clap": Impact("self_radius", "damage", "radius"),
    "obsidian_destroyer_astral_imprisonment": Impact("unit", "damage", delay="prison_duration"),
    "shadow_demon_shadow_poison": Impact("line", "hit_damage", speed="speed"),
    "chaos_knight_chaos_bolt": Impact("unit", "damage_min", speed="chaos_bolt_speed"),
    "ogre_magi_fireblast": Impact("unit", "fireblast_damage"),
    "rubick_fade_bolt": Impact("unit", "damage"),
    "keeper_of_the_light_blinding_light": Impact("point", "damage"),
    "medusa_mystic_snake": Impact("unit", "snake_damage", speed="initial_speed"),
    "centaur_hoof_stomp": Impact("self_radius", "stomp_damage", "radius", delay="windup_time"),
    "magnataur_shockwave": Impact("line", "shock_damage", speed="shock_speed"),
    "bristleback_quill_spray": Impact("self_radius", "quill_base_damage", "radius", speed="projectile_speed"),
    "skywrath_mage_arcane_bolt": Impact("unit", "bolt_damage", speed="bolt_speed"),
    "legion_commander_overwhelming_odds": Impact("self_radius", "damage", "radius"),
    "oracle_purifying_flames": Impact("unit", "damage"),
    "grimstroke_dark_artistry": Impact("line", "damage", speed="projectile_speed"),
    "void_spirit_resonant_pulse": Impact("self_radius", "damage", "radius", speed="speed"),
    "snapfire_scatterblast": Impact("line", "damage", speed="blast_speed"),
    "muerta_dead_shot": Impact("unit", "damage", speed="speed"),
    "largo_catchy_lick": Impact("unit", "damage"),
    "axe_culling_blade": Impact("unit", "damage"),
    "juggernaut_blade_fury": Impact("self_radius", "blade_fury_damage", "blade_fury_radius", delay="blade_fury_damage_tick", partial=True),
    "mirana_starfall": Impact("self_radius", "damage", "starfall_radius", partial=True),
    "razor_plasma_field": Impact("self_radius", "damage_min", "radius", partial=True),
    "tiny_avalanche": Impact("point", "avalanche_damage", speed="projectile_speed", partial=True),
    "puck_dream_coil": Impact("point", "coil_initial_damage", target="hero", partial=True),
    "kunkka_ghostship": Impact("point", "damages", delay="tooltip_delay"),
    "lion_impale": Impact("line", "damage", speed="speed"),
    "lion_finger_of_death": Impact("unit", "damage", delay="damage_delay", partial=True),
    "witch_doctor_paralyzing_cask": Impact("unit", "base_damage", speed="speed", partial=True),
    "lich_frost_nova": Impact("unit", "damage"),
    "lich_chain_frost": Impact("unit", "damage", speed="initial_projectile_speed", partial=True),
    "enigma_malefice": Impact("unit", "damage", partial=True),
    "sniper_shrapnel": Impact("point", "shrapnel_damage", delay="damage_delay", partial=True),
    "sniper_assassinate": Impact("unit", "damage", speed="projectile_speed", partial=True),
    "venomancer_noxious_plague": Impact("unit", "impact_damage", speed="projectile_speed", partial=True),
    "furion_sprout": Impact("unit", "sprout_damage"),
    "jakiro_ice_path": Impact("line", "damage", delay="path_delay"),
    "batrider_flamebreak": Impact("point", "damage_impact", speed="speed", partial=True),
    "spectre_spectral_dagger": Impact("line", "damage", speed="speed"),
    "gyrocopter_call_down": Impact("point", "damage", delay="missile_delay_tooltip", partial=True),
    "alchemist_acid_spray": Impact("point", "damage", delay="tick_rate", partial=True),
    "invoker_cold_snap": Impact("unit", "freeze_damage", partial=True),
    "invoker_tornado": Impact("line", "wex_damage", "travel_distance", speed="travel_speed", delay="lift_duration"),
    "invoker_deafening_blast": Impact("line", "damage", "travel_distance", speed="travel_speed"),
    "silencer_curse_of_the_silent": Impact("point", "application_damage", partial=True),
    "meepo_poof": Impact("self_radius", "poof_damage", "radius", partial=True),
    "undying_decay": Impact("point", "decay_damage", partial=True),
    "disruptor_thunder_strike": Impact("unit", "strike_damage", partial=True),
    "nyx_assassin_impale": Impact("line", "impale_damage", "length", speed="speed"),
    "visage_soul_assumption": Impact("unit", "soul_base_damage", speed="bolt_speed", partial=True),
    "troll_warlord_whirling_axes_ranged": Impact("line", "axe_damage", "axe_range", speed="axe_speed"),
    "shredder_whirling_death": Impact("self_radius", "whirling_damage", "whirling_radius", partial=True),
    "shredder_chakram": Impact("line", "pass_damage", speed="speed", partial=True),
    "ember_spirit_flame_guard": Impact("self_radius", "damage_per_second", "radius", delay="tick_interval", partial=True),
    "abyssal_underlord_firestorm": Impact("point", "wave_damage", delay="wave_interval", partial=True),
    "void_spirit_dissimilate": Impact("self_radius", "damages", "damage_radius", delay="phase_duration"),
    "dawnbreaker_celestial_hammer": Impact("line", "hammer_damage", "range", speed="projectile_speed", partial=True),
    "marci_grapple": Impact("unit", "impact_damage", delay="air_duration"),
    "brewmaster_cinder_brew": Impact("line", "barrel_impact_damage", speed="projectile_speed", partial=True),
    "ursa_earthshock": Impact("forward_circle", "damages", "shock_radius", delay="hop_duration"),
    "pangolier_shield_crash": Impact("forward_circle", "damage", "radius", delay="jump_duration", partial=True),
    "rattletrap_rocket_flare": Impact("point", "damage", "speed", speed="speed"),
    "warlock_shadow_word": Impact("unit", "damage", delay="tick_interval", partial=True),
    "weaver_shukuchi": Impact("self_radius", "damage", "radius"),
    "tusk_snowball": Impact("unit", "snowball_damage", speed="snowball_speed", delay="snowball_windup", partial=True),
    "elder_titan_echo_stomp": Impact("self_radius", "stomp_damage", "radius", delay="cast_time", partial=True),
    "ringmaster_tame_the_beasts": Impact("point", "damage_min", delay="crack_duration", partial=True),
    "pudge_rot": Impact("self_radius", "rot_damage", "rot_radius", delay="rot_tick", partial=True),
    "earth_spirit_magnetize": Impact("self_radius", "damage_per_second", "cast_radius", delay="damage_interval", partial=True),
    "primal_beast_pulverize": Impact("unit", "damage", delay="interval", partial=True),
    "phoenix_supernova": Impact("self_radius", "damage_per_sec", "aura_radius", delay="tick_interval", target="hero", partial=True),
    "antimage_mana_void": Impact("unit", "mana_void_damage_per_mana", target="hero", partial=True),
    "necrolyte_reapers_scythe": Impact("unit", "damage_per_health", delay="stun_duration", target="hero", partial=True),
}

# One observed, active Invoked button is required in addition to the level of
# each orb used by the effect. Each field is resolved from its own Valve series.
ORB_FIELDS = {
    "invoker_cold_snap": {"damage": ("invoker_quas", "freeze_damage")},
    "invoker_tornado": {"damage": ("invoker_wex", "wex_damage"), "reach": ("invoker_wex", "travel_distance"),
                        "delay": ("invoker_quas", "lift_duration")},
    "invoker_deafening_blast": {"damage": ("invoker_exort", "damage")},
}

FORWARD_OFFSET = {**{key: "shadowraze_range" for key, *_ in SHADOWRAZES},
                  "ursa_earthshock": "hop_distance", "pangolier_shield_crash": "jump_horizontal_distance"}

# Components which omit repeat hits, stacks, attribute multipliers or a second spell effect.
PARTIAL = {
    "chaos_knight_chaos_bolt", "queenofpain_shadow_strike", "bristleback_quill_spray",
    "skywrath_mage_arcane_bolt", "legion_commander_overwhelming_odds", "grimstroke_dark_artistry",
    "snapfire_scatterblast", "venomancer_venomous_gale", "shadow_demon_shadow_poison",
    "medusa_mystic_snake", "largo_catchy_lick", "obsidian_destroyer_astral_imprisonment",
    *(key for key, *_ in SHADOWRAZES),
}
INSTRUCTIONS = {
    "razor_plasma_field": "Считается минимум первого прохождения волны. Дистанция влияет на урон, обратная волна может задеть цель ещё раз.",
    "tiny_avalanche": "Считается один из пяти импульсов Avalanche. Полный урон зависит от времени в области и попадания Toss.",
    "ember_spirit_flame_guard": "Подойди в радиус активного Flame Guard. Оценён один тик, пока щит не сбит.",
    "sniper_shrapnel": "Считается первое попадание Shrapnel после задержки; нужен подтверждённый заряд.",
    "mirana_starfall": "Считается первая звезда. Второе попадание зависит от ближайшей подходящей цели.",
    "meepo_poof": "Примени Poof на себя. Считается одна область в исходной точке, без урона других Meepo.",
    "void_spirit_dissimilate": "Для этого попадания выйди через центральный портал Dissimilate.",
    "visage_soul_assumption": "Показан базовый урон без зарядов Soul Assumption.",
    "shredder_whirling_death": "Показан базовый чистый урон без срубленных деревьев и потери атрибута.",
    "undying_decay": "Показан общий базовый урон. Урон обычному крипу выше, к потере здоровья героя добавляется кража силы.",
    "sniper_assassinate": "Показан компонент заклинания. Дополнительно срабатывает атака Sniper.",
    "gyrocopter_call_down": "Наведи первый удар Call Down на цель. Следующие взрывы смещены.",
    "spectre_spectral_dagger": "Направь кинжал через позицию цели.",
    "pangolier_shield_crash": "Проверь зону приземления перед героем в обычной форме. Попадание по герою также даёт барьер.",
    "ursa_earthshock": "Проверь зону приземления перед Ursa.",
    "rattletrap_rocket_flare": "Rocket Flare действует по всей карте. Для сравнения на линии рассматривается полёт до одной секунды.",
    "warlock_shadow_word": "Учтён один тик Shadow Word на выбранном враге. Последующие тики требуют, чтобы эффект сохранился.",
    "weaver_shukuchi": "Цель уже находится в радиусе Shukuchi. Учтено одно касание.",
    "tusk_snowball": "При автоматическом запуске учитываются подготовка и полёт. Дополнительные союзники увеличивают урон.",
    "elder_titan_echo_stomp": "Оценивается физический удар самого героя после подготовки. Попадание духа требует отдельной проверки.",
    "ringmaster_tame_the_beasts": "Прицелься в точку и отпусти кнут в начале замаха. Показан минимальный урон.",
    "pudge_rot": "В этом кадре Rot выключен. Учтён первый тик по цели и базовый урон того же тика самому Pudge до его защиты. Дольше держать Rot дороже.",
    "earth_spirit_magnetize": "Считается первый интервал Magnetize по цели внутри начального радиуса. Обновления от каменных копий продлевают эффект.",
    "primal_beast_pulverize": "Считается первое опускание захваченной цели, если канал сохранится. Следующие удары усиливаются.",
    "phoenix_supernova": "Показан один тик солнца по герою. Phoenix превращается в яйцо, которое могут уничтожить атаками.",
    "antimage_mana_void": "Оценка по недостающей мане основной видимой цели в этом кадре. Запас маны к моменту попадания может измениться.",
    "necrolyte_reapers_scythe": "Оценка по недостающему здоровью цели в этом кадре. Урон в конце оглушения зависит от здоровья к тому моменту.",
}

NOTES = {
    **{key: "Ранги трёх койлов связаны. В расчёт входит базовый урон; души и предыдущие попадания могут его увеличить."
       for key, *_ in SHADOWRAZES},
    "puck_illusory_orb": "Альтернативное применение пускает шар по прямой. Для этой траектории учитывается урон от первого попадания.",
    "storm_spirit_static_remnant": "Ремнант материализуется через 0,75 с и может перемещаться. Проверь первого врага в радиусе срабатывания и кто попадёт в область взрыва.",
    "invoker_sun_strike": "Проверь уровень Exort и все вражеские цели в области при падении. Урон делится между ними.",
    "invoker_tornado": "Урон после приземления зависит от Wex, а время подъёма — от Quas.",
    "invoker_chaos_meteor": "Exort определяет урон от контакта и горения, Wex — путь метеора. Цель может уйти после первого касания.",
    "obsidian_destroyer_astral_imprisonment": "Урон приходит при выходе из заточения. Помещённый в астрал крип временно неуязвим.",
    "tinker_march_of_the_machines": "Число роботов, которые пересекут траекторию цели, заранее неизвестно.",
    "pugna_nether_blast": "Проверь здоровье крипа к моменту взрыва. Урон по строениям имеет отдельный множитель.",
    "rubick_fade_bolt": "Для проверки используется первый выбранный враг. На следующих отскоках урон уменьшается.",
    "queenofpain_shadow_strike": "Проверяется начальный удар. Последующие тики и повторное наложение разобраны отдельно.",
    "queenofpain_scream_of_pain": "Для размена учитывай полёт снаряда и отражение части урона по героям в саму Queen of Pain.",
    "ember_spirit_flame_guard": "Урон приходит тиками только пока действует щит и цель остаётся рядом.",
    "ember_spirit_searing_chains": "Цепи выбирают ограниченное число юнитов. Наличие врага в радиусе не гарантирует его выбор.",
    "ember_spirit_sleight_of_fist": "Удар зависит от атаки Ember. Бонус по герою нельзя прибавить к урону по крипу.",
    "ember_spirit_fire_remnant": "Установка ремнанта сама по себе не наносит урон взрыва. Проверь момент активации и число ремнантов.",
    "meepo_poof": "Проверь подготовку Poof и места отправления и прибытия каждого клона. Один и тот же крип может попасть в разные области.",
    "meepo_divided_we_stand": "В данных Valve для первого клона указан уровень 3. Проверь вложенное очко, положение копий и их независимые перезарядки.",
    "phantom_assassin_stifling_dagger": "Компонент атаки, крит и эффекты удара зависят от состояния героя.",
    "chaos_knight_chaos_bolt": "Для сравнения с HP используется нижняя граница случайного урона.",
    "tusk_ice_shards": "Проверь, не столкнётся ли снаряд с другим героем до нужной позиции.",
    "windrunner_powershot": "Урон зависит от времени подготовки и юнитов, уже пройденных стрелой.",
    "mirana_arrow": "Первый юнит на пути и пройденное расстояние меняют результат попадания.",
    "pudge_meat_hook": "Первый юнит по линии может закрыть нужную цель. Для обычного крипа действует особая механика попадания.",
    "obsidian_destroyer_arcane_orb": "Чистый урон зависит от оставшейся маны после оплаты способности. Проверь текущий запас и успеет ли долететь атака.",
    "sandking_scorpion_strike": "Stinger выполняет атаку и добавляет урон, который меняется в центре области.",
    "tiny_avalanche": "Указанный полный урон распределён по серии попаданий. Подброшенная цель получает другой урон.",
    "kunkka_torrent": "Цель должна остаться в области до подъёма. Урон распределяется во времени.",
    "shredder_whirling_death": "Число срубленных деревьев меняет урон, а потеря атрибута относится только к героям.",
    "templar_assassin_psi_blades": "Проверь основной удар, направление за первым крипом и положение соперника в момент попадания.",
    "skywrath_mage_arcane_bolt": "Проверяется базовая часть; интеллект может увеличить фактический урон.",
    "lina_dragon_slave": "В расчёте только начальная часть Dragon Slave. Периодическое горение рассматривается отдельно.",
    "oracle_purifying_flames": "После начального урона цель получает лечение. Это существенно для оценки размена.",
    "rattletrap_rocket_flare": "Для глобального снаряда задержка до попадания зависит от расстояния. Проверь, останется ли крип в области.",
}

HERO_FOCUS = {
    2: "Проверь счётчик Counter Helix, кто атакует Axe и может ли соперник снять Battle Hunger добиванием.",
    8: "Для Blade Fury измерь время контакта, а для Omnislash проверь, остались ли в зоне другие цели.",
    9: "Проверь, на кого падает вторая звезда Starstorm и какой юнит первым пересекает траекторию Sacred Arrow.",
    15: "Урон Plasma Field меняется с дистанцией. Для Static Link проверь время связи и атаки после кражи урона.",
    17: "Static Remnant должен успеть активироваться. Заряд Overload реализуется отдельным ударом.",
    26: "Сравни линию Earth Spike через дальнего крипа с применением по герою и проверь, хватит ли маны на продолжение.",
    31: "У Frost Blast различаются урон выбранной цели и урон вокруг неё. Посмотри, можно ли совместить добивание и харасс.",
    32: "Для размена проверь положение за спиной цели, доступные заряды Blink Strike и соперников внутри Tricks of the Trade.",
    37: "Для Fatal Bonds важно, кто связан и кому фактически наносят урон. Shadow Word реализуется тиками.",
    41: "Учитывай доступность Time Walk для восстановления после размена и случайные срабатывания Time Lock.",
    44: "Кинжал использует атаку героя. Проверь текущий урон, броню цели и шанс критического удара.",
    50: "Проверь соседние цели у Shadow Wave и сможет ли Dazzle продлевать Poison Touch последующими ударами.",
    51: "У Battery Assault удары могут уйти в крипов. Для Rocket Flare сравни полёт с моментом следующего удара по крипу.",
    54: "Проверь здоровье цели и сколько ударов Lifestealer успеет нанести под Open Wounds до отхода.",
    56: "Оцени Searing Arrows вместе с обычной атакой, бронёй цели и числом выстрелов за время Strafe.",
    57: "Purification требует подходящего союзника рядом с врагом. Для Hammer of Purity проверь атаку и доступ к цели.",
    58: "Для Impetus важна дистанция полёта, для Enchant — доступный крип, его атаки и способности.",
    64: "У Dual Breath и атак с эффектами разные моменты попадания. Для добивания проверь, когда пройдёт нужный тик.",
    67: "Проверь изоляцию цели для Desolate, траекторию Spectral Dagger и число доступных ударов после замедления.",
    68: "Cold Feet зависит от положения цели после наложения. Сравни дальность Chilling Touch и ответного удара.",
    69: "Проверь способность поглощённого крипа, готовность Infernal Blade и длительность контакта под Scorched Earth.",
    70: "Сколько стаков Fury Swipes уже на цели? Оцени число реальных ударов под Overpower и возможность догнать после Earthshock.",
    71: "Проверь траекторию Charge of Darkness и текущую скорость. Случайное срабатывание Greater Bash нельзя обещать.",
    72: "Крипы могут принять ракеты Rocket Barrage. Проверь изоляцию мидера и успеет ли соперник уничтожить Homing Missile.",
    75: "Учти текущий интеллект при размене Glaives of Wisdom и сколько времени соперник проведёт под Arcane Curse.",
    83: "Проверь положение деревьев и время контакта на Nature’s Grasp. Во время Leech Seed сравни также восстановление здоровья.",
    85: "Для Decay раздели урон заклинания и кражу силы у героев. Для Soul Rip посчитай доступных юнитов рядом.",
    87: "Thunder Strike проходит серией. Перед Glimpse найди прошлую позицию соперника и проверь, куда он вернётся.",
    88: "Для Impale проверь линию и время приземления. Для Mind Flare сверь недавний полученный целью урон.",
    89: "Посчитай доступные иллюзии, их атаки и фактический момент Rip Tide перед спорным добиванием.",
    91: "Для Spirits проверь радиус орбиты и столкновения с героем или крипом. Tether меняет размен при наличии союзника.",
    93: "Для Dark Pact учитывай задержку и отдельные импульсы. Силу затяжного размена меняют стаки Essence Shift.",
    100: "Траекторию Ice Shards может перекрыть герой. Для Snowball проверь длительность сближения и участников.",
    102: "Сравни расход собственного здоровья на Mist Coil с разменом через Aphotic Shield и моментом взрыва щита.",
    103: "Проверь положения героя и Astral Spirit при Echo Stomp и усиление атаки после возвращения духа.",
    105: "Для Sticky Bomb проверь, кто оказался рядом при прикреплении и взрыве. Для Blast Off! важна позиция приземления.",
    108: "Отдели волны Firestorm от горения. Для добивания атакой проверь текущий бонус Atrophy Aura.",
    110: "Проверь собственное здоровье до применения и число тиков Fire Spirits; замедление атаки влияет на ответные удары.",
    119: "Для Shadow Realm проверь зарядку атаки. Для Bramble Maze важно, пересёк ли соперник конкретную ловушку.",
    123: "Для Acorn Shot найди доступные отскоки. Для Bushwhack проверь наличие дерева рядом с целью в момент попадания.",
    129: "Spear of Mars зависит от траектории и препятствия за целью. God's Rebuke использует атаку самого Mars.",
    131: "Проверь длительность подготовки Tame the Beasts, а у Impalement Arts — кто первым встретит кинжал.",
    135: "Проверь число попаданий Starbreaker, готовность Luminosity и то, как долго цель простоит на следе Celestial Hammer.",
    136: "Для Rebound нужен подходящий юнит. Для Dispose сверь положение после броска и возможность продолжить атаку.",
    137: "Проверь зарядку Onslaught и пройденный путь во время Trample. Число попаданий зависит от движения.",
    1: "Размен зависит от доступной маны противника, числа ударов Mana Break и возможности выйти через Blink.",
    6: "Раздели урон автоатаки, число попаданий Multishot и эффект стрел по замедленной цели.",
    11: "Койлы направлены перед героем. Сравни расстояния, души, число уже наложенных стаков и время на разворот.",
    14: "Проверь первый юнит на линии Hook и то, как долго цель останется в Rot.",
    18: "Storm Hammer открывает время для следующих ударов. Урон клива зависит от атаки и положения цели за крипом.",
    19: "Сравни число тиков Avalanche и кого Toss действительно поднимет. Одиночный крип и герой в связке получают разный урон.",
    21: "Сила Powershot зависит от времени зарядки и юнитов на пути. На размен также влияет доступность Windrun.",
    23: "Для Tidebringer нужны готовый усиленный удар и взаимное положение крипа и мидера. Для Torrent проверь задержку.",
    30: "Отскоки Cask и урон Maledict зависят от соседних целей и последующей потери здоровья.",
    33: "Проверь число эйдолонов, их текущую цель и чей снаряд добивает крипа.",
    34: "Laser даёт прямой компонент по выбранной цели. Для March отдельно важны геометрия и число столкновений.",
    35: "Сравни дальность автоатаки, возможный Headshot и время стояния врага в Shrapnel.",
    43: "Сравни попадание Carrion Swarm с тем, успеет ли соперник разорвать дистанцию Spirit Siphon.",
    46: "Проверь заряды Refraction, Meld и линию пробоя Psi Blades через первого юнита.",
    47: "На размен влияют стаки яда и время внутри Nethertoxin. Оцени отдельные тики.",
    48: "Lucent Beam наносит прямой урон. Отскоки атаки и цель каждого луча Eclipse требуют отдельной проверки.",
    53: "Для добивания важны и атака героя, и снаряды призванных юнитов. История ударов определяет последний.",
    55: "Ion Shell зависит от длительности контакта с носителем. Не переноси полный урон заклинания на один кадр.",
    59: "Проверь здоровье до применения Inner Fire и накопленные Burning Spears перед продолжением размена.",
    61: "Учти число пауков, положение паутин и доступность добивания перед появлением новых паучков.",
    63: "Для Shukuchi важна траектория, для Geminate Attack — готовность и время второго выстрела.",
    65: "Сначала сверь стаки Sticky Napalm и доступ к цели. Урон от Flamebreak и Firefly приходит в разное время.",
    66: "Силу определяют доступные приручённые крипы, их способности и положение относительно линии.",
    73: "Число тиков Acid Spray, броня цели и длительность подготовки Unstable Concoction меняют результат.",
    74: "Invoker получает заклинания из комбинаций Quas, Wex и Exort. Нужны реальные уровни сфер, вызванные кнопки, мана и готовый Invoke.",
    76: "Для Arcane Orb проверь текущую ману. Astral наносит урон при возвращении цели, а не в начале изгнания.",
    77: "Основной размен строится на ударах героя и волков, их позиции, скорости и подготовке формы.",
    80: "Сравни удары медведя и героя, позицию обоих и доступность контроля. Уровень без медведя может давать другой размен.",
    82: "Проверь, взят ли клон на нестандартном уровне, сколько копий доступно и куда завершится каждый Poof.",
    86: "Fade Bolt ослабляет последующие удары соперника. До расчёта чужого заклинания нужна запись о фактически украденной кнопке.",
    92: "Сверь заряды Soul Assumption и атаки фамильяров. Базовый урон без зарядов и полный залп существенно различаются.",
    95: "Сверь текущую стойку, версию топоров и число накопленных ударов по одной цели.",
    98: "Проверь число деревьев, линию Timber Chain и урон по атрибутам только на подходящей цели.",
    101: "Arcane Bolt усиливается интеллектом. Mystic Flare распределяет урон по героям в области.",
    104: "Overwhelming Odds зависит от числа юнитов. Проверь, стоял ли соперник в своей пачке в момент нажатия.",
    106: "Сопоставь удар Sleight по герою и крипу, число целей Chains, оставшийся щит и время в Flame Guard.",
    107: "Проверь доступный Stone Remnant и линию движения. Подход к камню и фактически задетые цели влияют на окно.",
    109: "Для размена важны форма Metamorphosis, активные иллюзии и возможность противника переждать усиление.",
    112: "Главная цель Splinter Blast не получает урон осколком. Найди вторую подходящую цель.",
    113: "Flux зависит от изоляции цели, Spark Wraith — от того, кто первым войдёт в область.",
    114: "Усиление Jingu и крит Boundless Strike зависят от стаков и текущего урона атаки.",
    120: "Отдельно проверь число ударов Swashbuckle и положение при приземлении Shield Crash.",
    126: "Радиус Resonant Pulse и место выхода из Dissimilate определяют, достанет ли урон до крипа или героя.",
    145: "Определи стойку и текущие доступные кнопки. Нельзя объединять умения разных стоек в один бесплатный размен.",
}

def strip_label(text):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", text))).strip(" :\n\t")


def rank_series(values):
    values = list(values or [])
    return values[:1] if values and len(set(values)) == 1 else values


def parse_proof(a, specials):
    rule = IMPACTS.get(a["name"])
    if not rule:
        return None
    orb_fields = ORB_FIELDS.get(a["name"], {})
    def values(field):
        seq = a[field] if field in ("damages", "cast_ranges") else specials[field]["values_float"]
        if any(field == value_field for _, value_field in orb_fields.values()):
            if len(seq) != 9 or any(v <= 0 for v in seq):
                raise ValueError(f"Invalid orb progression {a['name']}.{field}: {seq}")
            return [seq[0]]  # Replaced with the observed orb rank by resolved_impact.
        if not seq or len(seq) not in (1, a["max_level"]) or any(v <= 0 for v in seq):
            raise ValueError(f"Invalid impact field {a['name']}.{field}: {seq}")
        return rank_series(seq)
    target_mask = {"hero": 1, "creep": 2, "both": 3}[rule.target]
    if rule.shape == "unit" and not (a["target_type"] & target_mask == target_mask and a["target_team"] & 2):
        raise ValueError("Target filter changed for " + a["name"])
    result = {"shape": rule.shape, "target": rule.target, "damage": values(rule.damage),
            "damage_type": {1:"physical",2:"magical",4:"pure"}[a["damage"]], "reach": values(rule.reach),
            "speed": values(rule.speed) if rule.speed else None, "delay": values(rule.delay) if rule.delay else [0]}
    if rule.shape == "forward_circle":
        result.update(radius=result["reach"], offset=values(FORWARD_OFFSET[a["name"]]))
    key = a["name"]
    if key in ("juggernaut_blade_fury", "ember_spirit_flame_guard", "warlock_shadow_word", "earth_spirit_magnetize",
               "phoenix_supernova", "pudge_rot"):
        interval, = values(rule.delay)
        result["damage"] = [v * interval for v in result["damage"]]
    if key == "tiny_avalanche":
        ticks, = values("tick_count")
        result["damage"] = [v / ticks for v in result["damage"]]
    if key == "lich_frost_nova":
        result["damage"] = [v + splash for v, splash in zip(result["damage"], values("aoe_damage"), strict=True)]
    if orb_fields:
        result["orb_scaling"] = {field: {"orb": orb, "values": specials[source]["values_float"]}
                                 for field, (orb, source) in orb_fields.items()}
        if key == "invoker_tornado":
            base, = values("base_damage")
            damage = result["orb_scaling"]["damage"]
            damage["values"] = [base + bonus for bonus in damage["values"]]
            result["damage"] = damage["values"][:1]
    if key in ("ursa_earthshock", "pangolier_shield_crash", "void_spirit_dissimilate", "meepo_poof", "tusk_snowball"):
        result["requires_unrooted"] = True
    if key == "pangolier_shield_crash":
        result["inactive_buttons"] = ["pangolier_gyroshell_stop", "pangolier_rollup_stop"]
    if key == "sniper_shrapnel":
        result["requires_charge"] = True
    if key == "pudge_rot":
        result["requires_off"] = True
    if key in ("antimage_mana_void", "necrolyte_reapers_scythe"):
        result["missing_pool"] = "mana" if key == "antimage_mana_void" else "hp"
    # The hero feed defines the damage, but does not specify the exact time of
    # first impact after release. A missing delay must not be read as zero.
    if key in ("lion_impale", "nyx_assassin_impale", "mirana_starfall", "razor_plasma_field", "tiny_avalanche", "weaver_shukuchi"):
        result["delay"] = None
    if rule.partial or key in PARTIAL:
        result["partial"] = True
    if key in INSTRUCTIONS:
        result["instruction"] = INSTRUCTIONS[key]
    if a["name"] == "puck_illusory_orb":
        result["instruction"] = "Выпусти Illusory Orb по прямой альтернативным применением."
    if a["name"] == "storm_spirit_static_remnant":
        result["instruction"] = "Примени Static Remnant на себя. До материализации цель должна остаться в радиусе срабатывания."
    return result


def classify(a, specials, proof):
    """Classification is explanatory; only the explicit Impact table is scored."""
    if proof:
        return "direct"
    key = a["name"]
    if key.startswith("invoker_"):
        return "orb"
    if a["ability_is_innate"]:
        return "innate"
    if any(k in key for k in ("_summon_", "_conjure_image", "_force_of_nature", "_spawn_spiderlings", "_divided_we_stand")):
        return "summon"
    if any("damage_per_second" in k or k in ("damage_tick_rate","tick_damage","damage_per_tick","damage_interval",
                                              "duration_damage","tick_interval","damage_pers") for k in specials):
        return "tick"
    if int(a["behavior"]) & 2:
        return "passive"
    if a["damage"] == 1 and any("attack" in k or "crit" in k for k in specials):
        return "attack"
    return "conditional" if a["damage"] in (1, 2, 4) else "utility"


def rank_gates(a, specials):
    """Earliest conventional hero levels. Linked/hidden ranks need actual replay data."""
    if a["name"] == "meepo_divided_we_stand":
        return rank_series(specials["required_level"]["values_float"])
    if a["ability_is_innate"] or int(a["behavior"]) & 1 or a["name"].startswith(("invoker_", "kez_", "dark_willow_bedlam")):
        return []
    if a["type"] == 0 and a["max_level"] == 4:
        return [1, 3, 5, 7]
    if a["type"] == 1 and a["max_level"] == 3:
        return [6, 12, 18]
    return []


def compact_spell(a):
    specials = {s["name"]: s for s in a["special_values"] if s["values_float"]}
    proof = parse_proof(a, specials)
    kind = classify(a, specials, proof)
    parameters = []
    for field, special in specials.items():
        if field.startswith(("Ability", "scepter_", "shard_", "talent_")) or special.get("required_facet"):
            continue
        values = rank_series(special["values_float"])
        label = strip_label(special["heading_loc"])
        if a["name"] == "storm_spirit_static_remnant" and field in ("static_remnant_delay", "static_remnant_radius"):
            label = {"static_remnant_delay": "Задержка материализации, с", "static_remnant_radius": "Радиус срабатывания"}[field]
        if a["name"].startswith("nevermore_shadowraze") and field == "shadowraze_radius":
            label = "Радиус области"
        if a["name"] == "obsidian_destroyer_arcane_orb" and field == "mana_pool_damage_pct":
            # The Russian heading says maximum, but Valve's mechanic description,
            # English text and both languages' notes specify remaining mana.
            label = "Доля оставшейся маны, преобразуемая в урон, %"
        if not label or not any(values) or (proof and field == IMPACTS[a["name"]].damage):
            continue
        parameters.append({"field": field, "label": label, "values": values})
    if not proof and any(a["damages"]):
        parameters.insert(0, {"field": "AbilityDamage", "label": "Урон из таблицы способности",
                              "values": rank_series(a["damages"])})
    health_cost = specials.get("health_cost", {}).get("values_float") or a["health_costs"]
    if a["name"] == "pudge_rot":
        health_cost = proof["damage"]
    return {"key": a["name"], "id": a["id"], "name": a["name_loc"], "max_rank": a["max_level"],
            "ultimate": a["type"] == 1, "innate": a["ability_is_innate"], "mechanic": kind,
            "rank_gates": rank_gates(a, specials),
            "proof": proof, "parameters": parameters, "note": NOTES.get(a["name"], ""),
            "cast_point": rank_series(a["cast_points"]), "mana": rank_series(a["mana_costs"]),
            "health_cost": rank_series(health_cost), "range": rank_series(a["cast_ranges"]),
            "cooldown": rank_series(a["cooldowns"])}


def hero_buttons(hero):
    for ability in hero["abilities"]:
        if ability["name"] != "nevermore_shadowraze1":
            yield ability
            continue
        if any(a["name"] in ("nevermore_shadowraze2", "nevermore_shadowraze3") for a in hero["abilities"]):
            raise ValueError("Valve now lists separate Shadowraze buttons; review the supplement.")
        for key, ability_id, label, offset in SHADOWRAZES:
            fields = [dict(s, values_float=[offset]) if s["name"] == "shadowraze_range" else s
                      for s in ability["special_values"]]
            yield dict(ability, id=ability_id, name=key, name_loc=label, special_values=fields)


def compact_hero(hero):
    abilities = [compact_spell(a) for a in hero_buttons(hero) if a["max_level"] >= 1
                 and not a["ability_is_granted_by_scepter"] and not a["ability_is_granted_by_shard"]]
    focus = HERO_FOCUS.get(hero["id"])
    if not focus:
        impact = next((a for a in abilities if a["proof"]), None)
        if impact:
            focus = f"Сравни два применения {impact['name']} — для добивания дальнего крипа и для размена с мидером. Проверь ответ соперника на перезарядке."
        else:
            names = ", ".join(a["name"] for a in abilities if not a["innate"] and not a["ultimate"])
            focus = f"Сверь вложенные очки в {names} и доступные условия применения перед подходом к спорной волне."
    result = {"name": hero["name_loc"], "focus": focus, "abilities": abilities}
    if hero["id"] == 11:
        result["game_source"] = SHADOWRAZE_GAME_FILE
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-dir", type=Path, help="Saved Russian Valve hero_ID.json profiles from this patch")
    parser.add_argument("--out", type=Path, default=ROOT / "pro_replay/mid_spellbook.json")
    args = parser.parse_args()
    policy = make_policy(valve_patches(), as_of=int(time()))
    if policy["patch"] != PATCH:
        raise ValueError("The damage component and geometry review applies only to " + PATCH)
    def fetch(endpoint):
        with urlopen(URL + endpoint, timeout=30) as response:
            return json.load(response)["result"]["data"]["heroes"]
    ids = sorted(h["id"] for h in fetch("herolist?language=russian"))
    def profile(i):
        if args.from_dir:
            h = json.loads((args.from_dir / f"hero_{i}.json").read_bytes())
        else:
            h, = fetch(f"herodata?language=russian&hero_id={i}")
        if h["id"] != i:
            raise ValueError("Unexpected hero ID")
        return compact_hero(h)
    with ThreadPoolExecutor(4) as pool:
        heroes = dict(zip(ids, pool.map(profile, ids)))
    names = {s["key"] for h in heroes.values() for s in h["abilities"] if s["proof"]}
    if names != IMPACTS.keys() or make_policy(valve_patches(), as_of=int(time()))["patch"] != PATCH:
        raise ValueError("Valve roster, abilities or active patch changed during the build.")
    coverage = {"heroes": len(heroes), "abilities": sum(len(h["abilities"]) for h in heroes.values()),
                "impact_abilities": len(names),
                "impact_heroes": sum(any(s["proof"] for s in h["abilities"]) for h in heroes.values())}
    result = {"format": 1, "patch": PATCH, "source": URL + "herodata?language=russian&hero_id={id}",
              "coverage": coverage, "heroes": heroes}
    output = args.out.with_suffix(".tmp")
    output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf8")
    output.replace(args.out)


if __name__ == "__main__":
    main()
