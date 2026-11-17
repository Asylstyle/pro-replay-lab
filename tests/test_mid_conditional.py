import unittest

from pro_replay.mid_conditional import after_defense, attack_option


def actor(hero_id, key=None, level=1, cost=0):
    buttons = {}
    if key:
        buttons[key] = {"kind": "ability", "level": level, "mana_cost": cost, "active": True, "cooldown": 0}
    return {"hero_id": hero_id, "alive": True, "unit_state": 0, "x": 0, "y": 0, "mana": 400, "hp": 600,
            "max_hp": 700, "attack_min": 50, "attack_max": 56, "attack_bonus": 2, "attack_range": 500,
            "buttons": buttons, "intellect_total": 40}


def target(hero=True):
    unit = {"hp": 400, "takes_damage": True, "unit_state": 0, "x": 320, "y": 0, "armor": 0,
            "magic_resist_pct": 25}
    if hero:
        unit.update(hero_id=46, alive=True, intellect_total=30, agility_total=30)
    else:
        unit["name"] = "npc_dota_creep_badguys_ranged"
    return unit


class ConditionalAttackTests(unittest.TestCase):
    def test_attribute_defenses_and_distinct_packets(self):
        hero = target()
        self.assertEqual(after_defense(100, "physical", hero), 76)
        self.assertEqual(after_defense(100, "magical", hero), 72)
        self.assertEqual(after_defense(100, "physical", target(False)), 100)
        self.assertEqual(after_defense(100, "pure", hero), 100)
        hero.pop("intellect_total")
        self.assertIsNone(after_defense(100, "magical", hero))
        self.assertIsNone(after_defense(float("nan"), "pure", hero))

    def test_measured_damage_once_and_unknown_swing(self):
        h, t = actor(3), target(False)
        option = attack_option(h, t, 3, target_is_hero=False)
        self.assertEqual((option["damage_min"], option["damage_max"], option["ready"]), (52, 58, None))
        self.assertIsNone(attack_option(h, dict(t, x=600), 3, target_is_hero=False))
        self.assertIsNone(attack_option(h, dict(t, unit_state=1 << 2), 3, target_is_hero=False))
        self.assertIsNone(attack_option(dict(h, unit_state=1 << 32), t, 3, target_is_hero=False))
        self.assertIsNone(attack_option(dict(h, attack_bonus=None), t, 3, target_is_hero=False))
        self.assertIsNone(attack_option(h, dict(t, name="npc_dota_creep_goodguys_melee"), 3, target_is_hero=False))

    def test_orb_uses_remaining_mana_frost_is_physical(self):
        od = actor(76, "obsidian_destroyer_arcane_orb", cost=80)
        option = attack_option(od, target(), 76, target_is_hero=True)
        self.assertEqual((option["damage_min"], option["cost"], option["ready"]), (40 + 32, 80, None))
        od["buttons"]["obsidian_destroyer_arcane_orb"]["mana_cost"] = 0
        self.assertEqual(attack_option(od, target(), 76, target_is_hero=True)["damage_min"], 72)
        od["buttons"]["obsidian_destroyer_arcane_orb"]["cooldown"] = 3
        self.assertEqual(attack_option(od, target(), 76, target_is_hero=True)["name"], "Обычная атака")
        frost = actor(6, "drow_ranger_frost_arrows", cost=9)
        self.assertEqual(attack_option(frost, target(), 6, target_is_hero=True)["damage_min"], 49)
        silencer = actor(75, "silencer_glaives_of_wisdom", cost=12)
        self.assertEqual(attack_option(silencer, target(), 75, target_is_hero=True)["damage_min"], 40 + 10)

    def test_spell_generated_attacks_use_own_range_and_cost(self):
        t = dict(target(), x=600)
        ember = actor(106, "ember_spirit_sleight_of_fist", cost=75)
        opt = attack_option(ember, t, 106, target_is_hero=True)
        self.assertEqual((opt["damage_min"], opt["ready"], opt["arrival"]), (70, True, None))
        self.assertEqual(attack_option(ember, target(False), 106, target_is_hero=False)["damage_min"], 52)
        self.assertIsNone(attack_option(ember, t, 106, target_is_hero=True, include_orb=False))
        self.assertIsNone(attack_option(ember, t, 106, target_is_hero=True, clean_range=False))
        self.assertIsNone(attack_option(dict(ember, unit_state=1), t, 106, target_is_hero=True))
        dagger = actor(44, "phantom_assassin_stifling_dagger", cost=30)
        pa = attack_option(dagger, dict(target(False), x=600), 44, target_is_hero=False)
        self.assertEqual(pa["damage_min"], 80)
        self.assertAlmostEqual(pa["arrival"], .8)
        hoodwink = actor(123, "hoodwink_acorn_shot", cost=70)
        shot = attack_option(hoodwink, dict(target(False), x=550), 123, target_is_hero=False)
        self.assertEqual((shot["damage_min"], shot["ready"], shot["partial"]), (86, None, True))
        self.assertAlmostEqual(shot["arrival"], .45)
        mk = actor(114, "monkey_king_boundless_strike", cost=85)
        self.assertEqual(attack_option(mk, target(False), 114, target_is_hero=False)["damage_min"], 82)

    def test_preparations_and_dots_keep_the_first_hit_separate(self):
        sniper = actor(35, "sniper_take_aim")
        self.assertEqual(attack_option(sniper, target(), 35, target_is_hero=True)["key"], "attack")
        sniper["buttons"]["sniper_headshot"] = {"kind":"ability", "level":2}
        shot = attack_option(sniper, target(), 35, target_is_hero=True)
        self.assertIn("Headshot", shot["name"])
        self.assertIsNone(shot["ready"])
        ta = actor(46, "templar_assassin_meld", cost=35)
        self.assertIn("Meld", attack_option(ta, target(), 46, target_is_hero=True)["name"])
        self.assertEqual(attack_option(dict(ta, unit_state=1 << 7), target(), 46, target_is_hero=True)["key"], "attack")
        huskar = actor(59, "huskar_burning_spear")
        option = attack_option(huskar, target(), 59, target_is_hero=True)
        self.assertEqual(option["health_cost"], 14)
        self.assertEqual(option["damage_min"], 40)
        self.assertTrue(option["partial"])
        self.assertIn("в секунду", option["note"])
        self.assertEqual(attack_option(huskar, target(False), 59, target_is_hero=False)["key"], "attack")

    def test_mars_current_cone_and_tusk_attack_timer(self):
        mars = actor(129, "mars_gods_rebuke", cost=90)
        mars.update(attack_range=150, facing_yaw=0)
        t = target()
        bash = attack_option(mars, t, 129, target_is_hero=True)
        self.assertEqual((bash["damage_min"], bash["ready"]), (63, True))
        self.assertIsNone(attack_option(dict(mars, facing_yaw=180), t, 129, target_is_hero=True))
        self.assertIsNone(attack_option(dict(mars, facing_yaw=None), t, 129, target_is_hero=True))
        tusk = actor(100, "tusk_walrus_punch", cost=75)
        punch = attack_option(tusk, target(False), 100, target_is_hero=False)
        self.assertEqual((punch["damage_min"], punch["damage_max"], punch["ready"]), (164, 236, None))
