import unittest
from unittest.mock import patch

from pro_replay.mid_spells import hero_profile, rank_value, render_spellbook, spell_catalog, spell_specs
from scripts.build_mid_spells import IMPACTS, Impact, parse_proof


class MidSpellCatalogTests(unittest.TestCase):
    def test_all_kits_and_first_impact_contract(self):
        catalog = spell_catalog()
        self.assertEqual(catalog["patch"], "7.41f")
        heroes = catalog["heroes"]
        spells = [s for hero in heroes.values() for s in hero["abilities"]]
        exact = [s for s in spells if s["proof"]]
        self.assertEqual(catalog["coverage"], {
            "heroes": len(heroes), "abilities": len(spells), "impact_abilities": len(exact),
            "impact_heroes": sum(any(s["proof"] for s in h["abilities"]) for h in heroes.values()),
        })
        self.assertEqual(len(heroes), 127)
        self.assertEqual(set(IMPACTS), {s["key"] for s in exact})
        for hero_id, hero in heroes.items():
            with self.subTest(hero=hero_id):
                self.assertEqual(len(hero["abilities"]), len(spell_specs(hero_id)))
                self.assertTrue(hero["focus"])
        for spell in exact:
            with self.subTest(spell=spell["key"]):
                proof = spell["proof"]
                self.assertIn(proof["shape"], ("unit", "self_radius", "point", "line", "forward_circle"))
                for field in ("reach", "damage", "delay"):
                    self.assertIn(len(proof[field]), (1, spell["max_rank"]))
                self.assertIn(proof["damage_type"], ("magical", "physical", "pure"))

    def test_directional_variants_and_point_abilities(self):
        sf = spell_specs(11)
        for i, distance in enumerate((200, 450, 700), start=1):
            ability = sf[f"nevermore_shadowraze{i}"]
            self.assertEqual(ability["id"], 5058 + i)
            self.assertEqual(ability["proof"]["offset"], [distance])
            self.assertEqual(ability["proof"]["radius"], [250])
            self.assertEqual(ability["proof"]["damage"], [85, 150, 215, 280])
        orb = spell_specs(13)["puck_illusory_orb"]["proof"]
        self.assertIn("прямой", orb["instruction"])
        self.assertEqual(orb["speed"], [750])
        remnant = spell_specs(17)["storm_spirit_static_remnant"]["proof"]
        self.assertEqual((remnant["reach"], remnant["delay"]), ([235], [0.75]))
        self.assertIn("себя", remnant["instruction"])
        self.assertEqual(spell_specs(76)["obsidian_destroyer_astral_imprisonment"]["proof"]["delay"],
                         [1.75, 2.5, 3.25, 4])
        self.assertEqual(spell_specs(96)["centaur_hoof_stomp"]["proof"]["delay"], [0.5])

    def test_initial_hit_vs_periodic_and_stat_based_damage(self):
        dagger = spell_specs(39)["queenofpain_shadow_strike"]
        self.assertEqual(dagger["proof"]["damage"], [50, 80, 110, 140])
        self.assertNotEqual(next(p["values"] for p in dagger["parameters"] if p["field"] == "duration_damage"),
                            dagger["proof"]["damage"])
        self.assertEqual(spell_specs(59)["huskar_inner_fire"]["health_cost"], [75, 100, 125, 150])
        for spell in ("ember_spirit_searing_chains", "ember_spirit_sleight_of_fist", "ember_spirit_flame_guard"):
            self.assertIsNone(spell_specs(106)[spell]["proof"])
        sun = spell_specs(74)["invoker_sun_strike"]
        damage, = [p for p in sun["parameters"] if p["field"] == "damage"]
        self.assertIn("EXORT", damage["label"])
        self.assertEqual(len(damage["values"]), 9)
        self.assertIsNone(sun["proof"])

    def test_rank_gates_and_one_frame_rank(self):
        self.assertEqual(spell_specs(86)["rubick_fade_bolt"]["rank_gates"], [1, 3, 5, 7])
        self.assertEqual(spell_specs(82)["meepo_divided_we_stand"]["rank_gates"], [3])
        self.assertFalse(spell_specs(74)["invoker_sun_strike"]["rank_gates"])
        self.assertIn("Ранг в выбранном кадре — 2", render_spellbook(86, learned={"rubick_fade_bolt": 2}))
        self.assertNotIn("Ранг в выбранном кадре", render_spellbook(86))
        self.assertNotIn("Ранг в выбранном кадре", render_spellbook(86, learned={"rubick_fade_bolt": 8}))
        self.assertFalse(hero_profile(98765))
        self.assertEqual(render_spellbook(98765), "")
        self.assertEqual(rank_value([250], 4), 250)
        self.assertEqual(rank_value([50, 80, 110, 140], 2), 80)
        for bad in (0, -1, True, 3.4, None, 5):
            self.assertIsNone(rank_value([50, 80, 110, 140], bad))

    def test_rebuild_rejects_changed_unit_filter(self):
        ability = {"name": "example", "target_type": 1, "target_team": 2}
        with patch.dict(IMPACTS, {"example": Impact("unit", "damage")}):
            with self.assertRaisesRegex(ValueError, "Target filter"):
                parse_proof(ability, {})
