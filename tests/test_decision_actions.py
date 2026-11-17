import unittest

from pro_replay.decision_actions import decision_options
from pro_replay.fight_coach import analyze_fights
from test_fight_coach import HEROES, actor, match, window


class DecisionActionsTests(unittest.TestCase):
    def setUp(self):
        self.heroes = {**HEROES, 3: {"name": "npc_dota_hero_bane", "localized_name": "Bane"},
                       86: {"name": "npc_dota_hero_rubick", "localized_name": "Rubick"}}
        self.raw = match()
        self.parts = self.raw["teamfights"][0]["players"]
        actor(self.raw, 0)["hero_id"] = 3
        self.parts[0].update(ability_uses={"bane_fiends_grip": 1},
                             ability_targets={"bane_fiends_grip": {"npc_dota_hero_ursa": 1}})
        self.parts[1]["ability_uses"] = {}
        self.parts[6].update(deaths=0, ability_uses={})
        self.parts[5]["deaths"] = 1
        actor(self.raw, 129)["deaths_log"] = []
        actor(self.raw, 128)["deaths_log"] = [{"time": 1219, "key": "npc_dota_hero_bane", "time_dead": 40}]
        self.earlier = window(1050, 1090)
        self.raw["teamfights"].insert(0, self.earlier)

    def options(self):
        fight = next(f for f in analyze_fights(self.raw, 129, self.heroes) if f["start"] == 1200)
        return decision_options(self.raw, 129, self.heroes, fight)

    def test_previous_hex_can_interrupt_channel_on_dying_ally(self):
        option, = self.options()
        self.assertEqual((option["kind"], option["recipient_side"], option["button"], option["target"],
                          option["event_time"], option["event_kind"], option["event_target_slot"]),
                         ("interrupt", "enemy", "Hex", "Bane", 1219, "death", 128))
        self.assertIn("Ursa", option["evidence"])
        self.assertIn("Fiend", option["action"])
        self.assertIn("перезарядку", option["check"])
        self.assertIn("пока канал ещё шёл", option["check"])
        self.assertEqual(option["strength"], 3)

    def test_skill_learned_later_or_previously_stolen_is_not_assumed_available(self):
        self.earlier.update(start=1260, end=1280)
        self.assertEqual(self.options(), [])
        self.earlier.update(start=1050, end=1090)
        actor(self.raw, 129)["hero_id"] = 86
        self.assertEqual(self.options(), [])

    def test_unknown_skill_data_never_counts_as_zero(self):
        self.parts[6]["ability_uses"] = None
        self.assertEqual(self.options(), [])

    def test_compare_actual_cast_target_or_flag_unknown_recipient(self):
        self.parts[6]["ability_uses"] = {"lion_voodoo": 1}
        self.parts[6]["ability_targets"] = {"lion_voodoo": {"npc_dota_hero_skywrath_mage": 1}}
        actual, = self.options()
        self.assertIn("Skywrath Mage", actual["evidence"])
        self.assertEqual(actual["strength"], 3)
        self.parts[6]["ability_targets"] = {}
        actor(self.raw, 129)["ability_targets"] = {"lion_voodoo": {"npc_dota_hero_bane": 10}}
        unknown, = self.options()
        self.assertEqual(unknown["strength"], 1)
        self.assertIn("Если это был Bane", unknown["check"])
        self.parts[6]["ability_targets"] = {"lion_voodoo": {"npc_dota_hero_bane": 1}}
        self.assertEqual(self.options(), [])

    def test_damage_already_done_to_suggested_target_beats_inconsistent_cast_counts(self):
        self.earlier["players"][6]["ability_uses"] = {"lion_impale": 1}
        self.parts[6].update(ability_uses={"lion_impale": 1},
                             ability_targets={"lion_impale": {"npc_dota_hero_skywrath_mage": 1}})
        me = actor(self.raw, 129)
        me["max_hero_hit"] = {"time": 1212, "key": "npc_dota_hero_bane", "unit": "npc_dota_hero_lion",
                              "player_slot": 129, "value": 150, "inflictor": "lion_impale"}
        self.assertEqual(self.options(), [])
        self.parts[6]["ability_uses"] = {}
        self.assertEqual(self.options(), [])

    def test_prior_purchase_can_protect_named_ally_when_item_log_is_complete(self):
        self.parts[0]["ability_uses"] = {}
        self.parts[1].update(ability_uses={"zuus_lightning_bolt": 1},
                             ability_targets={"zuus_lightning_bolt": {"npc_dota_hero_ursa": 1}})
        actor(self.raw, 128)["deaths_log"][0]["key"] = "npc_dota_hero_zuus"
        me = actor(self.raw, 129)
        purchase = {"key": "glimmer_cape", "time": 1000}
        me["purchase_log"] = [purchase]
        def glimmer():
            return [o for o in self.options() if o["button"] == "Glimmer Cape"]
        case, = glimmer()
        self.assertEqual((case["kind"], case["recipient_side"], case["target"], case["enemy_focus"],
                          case["target_slot"], case["focus_slot"]), ("protect", "ally", "Ursa", "Zeus", 128, 1))
        self.assertIn("Ursa", case["action"])
        for log in (None, {"glimmer_cape": 1}):
            self.parts[6]["item_uses"] = log
            self.assertEqual(glimmer(), [])
        self.parts[6]["item_uses"] = {}
        purchase["time"] = 1221
        self.assertEqual(glimmer(), [])
        purchase["time"] = 1000
        self.raw["match_id"] = 44556
        inventory = {"game_time": 1219, "item_id": [1, 50, 36, 178, 168, 17]}
        player_meta = {"player_slot": 129, "team_slot": 1, "inventory_snapshot": [inventory]}
        self.raw["metadata"] = {"data": {"match_id": "44556", "metadata": {
            "teams": [{"dota_team": 3, "players": [player_meta]}]}}}
        self.assertEqual(glimmer(), [])
        inventory["game_time"] -= 5
        self.assertTrue(glimmer())

    def test_shared_item_can_counter_second_caster_who_targeted_the_victim(self):
        actor(self.raw, 0)["hero_id"], actor(self.raw, 2)["hero_id"] = 101, 3
        self.parts[0].update(ability_uses={"skywrath_mage_arcane_bolt": 1},
                             ability_targets={"skywrath_mage_arcane_bolt": {"npc_dota_hero_lion": 1}})
        self.parts[1].update(ability_uses={"zuus_lightning_bolt": 1},
                             ability_targets={"zuus_lightning_bolt": {"npc_dota_hero_ursa": 1}})
        actor(self.raw, 128)["deaths_log"][0]["key"] = "npc_dota_hero_zuus"
        actor(self.raw, 129)["purchase_log"] = [{"key": "glimmer_cape", "time": 1000}]
        fight = next(f for f in analyze_fights(self.raw, 129, self.heroes) if f["start"] == 1200)
        shop, = [i for i in fight["items"] if i["key"] == "glimmer_cape"]
        self.assertEqual(shop["threat_hero"], "Skywrath Mage")
        choice, = [o for o in self.options() if o["button"] == "Glimmer Cape"]
        self.assertEqual((choice["enemy_focus"], choice["target"]), ("Zeus", "Ursa"))

    def test_selected_hero_or_enemy_with_logged_respawn_after_event_is_excluded(self):
        for dead in (129, 0):
            p = actor(self.raw, dead)
            p["deaths_log"].append({"time": 1190, "key": "", "time_dead": 40})
            self.assertEqual(self.options(), [])
            p["deaths_log"].clear()
        self.assertTrue(self.options())

    def test_self_defensive_buttons_already_used_are_not_recommended_again(self):
        actor(self.raw, 129)["hero_id"] = 70
        actor(self.raw, 128)["hero_id"] = 26
        actor(self.raw, 128)["deaths_log"] = []
        actor(self.raw, 129)["deaths_log"] = [{"time": 1219, "key": "npc_dota_hero_bane"}]
        self.parts[5]["deaths"], self.parts[6]["deaths"] = 0, 1
        self.earlier["players"][6]["ability_uses"] = {"ursa_enrage": 1}
        before, = self.options()
        self.assertEqual((before["target"], before["enemy_focus"], before["kind"]), ("Ursa", "Bane", "self_defense"))
        self.parts[6]["ability_uses"] = {"ursa_enrage": 1}
        self.assertEqual(self.options(), [])
