import unittest
from unittest.mock import patch

from pro_replay.mid_spells import hero_profile, impact_damage, rank_value, render_spellbook, resolved_impact, spell_catalog, spell_specs
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
                for field in ("reach", "damage"):
                    self.assertIn(len(proof[field]), (1, spell["max_rank"]))
                if proof["delay"] is not None:
                    self.assertIn(len(proof["delay"]), (1, spell["max_rank"]))
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
        for spell in ("ember_spirit_searing_chains", "ember_spirit_sleight_of_fist"):
            self.assertIsNone(spell_specs(106)[spell]["proof"])
        guard = spell_specs(106)["ember_spirit_flame_guard"]["proof"]
        self.assertEqual(guard["damage"], [4, 6, 8, 10])
        self.assertTrue(guard["partial"])
        self.assertEqual(guard["delay"], [0.2])
        sun = spell_specs(74)["invoker_sun_strike"]
        damage, = [p for p in sun["parameters"] if p["field"] == "damage"]
        self.assertIn("EXORT", damage["label"])
        self.assertEqual(len(damage["values"]), 9)
        self.assertIsNone(sun["proof"])

    def test_orbs_resolve_only_for_invoked_buttons_without_mutating_catalog(self):
        spell = spell_specs(74)["invoker_tornado"]
        rank = lambda level: {"kind": "ability", "level": level}
        buttons = {"invoker_quas": rank(2), "invoker_wex": rank(3),
                   "invoker_tornado": dict(rank(1), active=True, slot=4)}
        actor = {"hero_id": 74, "buttons": buttons}
        result = resolved_impact(spell, actor, 1)["proof"]
        self.assertEqual((result["damage"], result["reach"], result["delay"]), ([185], [2100], [1.4]))
        self.assertEqual(spell["proof"]["damage"], [95])
        buttons["invoker_tornado"]["active"] = False
        self.assertIsNone(resolved_impact(spell, actor, 1))
        buttons["invoker_tornado"].update(active=True, slot=7)
        self.assertIsNone(resolved_impact(spell, actor, 1))
        buttons["invoker_tornado"]["slot"] = 3
        buttons["invoker_quas"]["level"] = 0
        self.assertIsNone(resolved_impact(spell, actor, 1))

    def test_guarded_charges_and_jump_geometry(self):
        spell = spell_specs(35)["sniper_shrapnel"]
        buttons = {spell["key"]: {}}
        actor = {"buttons": buttons, "unit_state": 0}
        self.assertIsNone(resolved_impact(spell, actor, 2))
        buttons[spell["key"]]["charges"] = 1
        self.assertEqual(resolved_impact(spell, actor, 2)["proof"]["damage"][1], 45)
        buttons[spell["key"]]["charges"] = 0
        self.assertIsNone(resolved_impact(spell, actor, 2))
        crash = spell_specs(120)["pangolier_shield_crash"]
        self.assertIsNone(resolved_impact(crash, actor, 2))
        buttons.update({key: {"active": False} for key in crash["proof"]["inactive_buttons"]})
        self.assertEqual(resolved_impact(crash, actor, 2)["proof"]["offset"], [225])
        actor["unit_state"] = 1  # Rooted.
        self.assertIsNone(resolved_impact(crash, actor, 2))

    def test_single_hits_and_uncertain_timing(self):
        frost = spell_specs(31)["lich_frost_nova"]["proof"]
        self.assertEqual(frost["damage"], [120, 200, 280, 360])  # Both components on the primary target.
        toss = spell_specs(19)["tiny_avalanche"]["proof"]
        self.assertEqual(toss["damage"], [18, 36, 54, 72])
        self.assertIsNone(toss["delay"])
        self.assertTrue(toss["partial"])

    def test_missing_health_and_mana_only_use_the_observed_pool(self):
        mana = spell_specs(1)["antimage_mana_void"]["proof"]
        hp = spell_specs(36)["necrolyte_reapers_scythe"]["proof"]
        self.assertEqual(impact_damage(mana, 1, {"mana":220, "max_mana":600}), 380)
        self.assertEqual(impact_damage(hp, 2, {"hp":400, "max_hp":1000}), 480)
        self.assertEqual(impact_damage(mana, 2, {"mana":100, "max_mana":100}), 0)
        for bad in ({"mana":500}, {"mana":601, "max_mana":600}, {"mana":True, "max_mana":100},
                    {"mana":float("nan"), "max_mana":500}):
            self.assertIsNone(impact_damage(mana, 1, bad))
        self.assertIn("за единицу недостающего здоровья", render_spellbook(36))

    def test_rot_requires_recorded_off_state_and_prices_own_tick(self):
        rot = spell_specs(14)["pudge_rot"]
        self.assertEqual(rot["proof"]["damage"], [6, 12, 18, 24])
        self.assertEqual(rot["health_cost"], rot["proof"]["damage"])
        for state, active in (({}, False), ({"toggled":True},False), ({"toggled":False},True)):
            actor = {"buttons":{"pudge_rot":state}}
            self.assertEqual(resolved_impact(rot, actor, 2) is not None, active)

    def test_first_interval_and_excluded_collateral(self):
        mag = spell_specs(107)["earth_spirit_magnetize"]["proof"]
        slam = spell_specs(137)["primal_beast_pulverize"]["proof"]
        egg = spell_specs(110)["phoenix_supernova"]["proof"]
        self.assertEqual((mag["damage"], mag["delay"]), ([45,90,135], [1]))
        self.assertEqual((slam["damage"], slam["delay"]), ([100,175,250], [0.75]))
        self.assertEqual((egg["damage"], egg["delay"], egg["target"]), ([10,16,22], [0.2], "hero"))
        for proof in (mag,slam,egg):
            self.assertTrue(proof["partial"])

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
