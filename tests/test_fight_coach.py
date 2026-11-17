from copy import deepcopy
import unittest

from pro_replay.fight_coach import analyze_fights
from pro_replay.fight_review import SLOTS
from pro_replay.fight_review import _spell_name


HEROES = {
    22: {"name": "npc_dota_hero_zuus", "localized_name": "Zeus"},
    70: {"name": "npc_dota_hero_ursa", "localized_name": "Ursa"},
    26: {"name": "npc_dota_hero_lion", "localized_name": "Lion"},
    104: {"name": "npc_dota_hero_legion_commander", "localized_name": "Legion Commander"},
    101: {"name": "npc_dota_hero_skywrath_mage", "localized_name": "Skywrath Mage"},
}


def actor(raw, slot):
    return next(p for p in raw["players"] if p["player_slot"] == slot)


def window(start=1200, end=1240):
    players = [dict(damage=0, healing=0, deaths=0, item_uses={}, ability_uses={}) for _ in SLOTS]
    players[0]["ability_uses"] = {"legion_commander_duel": 1}
    players[1]["ability_uses"] = {"zuus_lightning_bolt": 2}
    players[6].update(ability_uses={"lion_voodoo": 2}, damage=350, deaths=1)
    return {"start": start, "end": end, "players": players}


def match():
    lineup = (104, 22, 101, 30, 12, 70, 26, 49, 4, 16)
    raw = {"players": [{"player_slot": s, "hero_id": h, "kills_log": [], "deaths_log": [],
                        "purchase_log": [], **{f"item_{i}": 0 for i in range(6)}}
                       for s, h in zip(SLOTS, lineup)], "teamfights": [window()]}
    raw["players"].reverse()
    actor(raw, 129).update(position_est=5, deaths_log=[{"time": 1219, "key": "npc_dota_hero_zuus"}])
    return raw


class FightCoachTests(unittest.TestCase):
    def test_sentry_placement_uses_ward_log_and_combined_dispenser_is_ambiguous(self):
        raw = match()
        f = raw["teamfights"][0]["players"]
        f[0]["ability_uses"] = {"bounty_hunter_wind_walk": 1}
        f[1]["ability_uses"] = {}
        f[6]["item_uses"] = {"ward_dispenser": 1}
        me = actor(raw, 129)
        me.update(purchase_log=[{"key": "ward_sentry", "time": 200}])
        result, = analyze_fights(raw, 129, HEROES)
        sentry = next(i for i in result["items"] if i["key"] == "ward_sentry")
        self.assertIn("журнал установок Sentry недоступен", sentry["evidence"])
        me["sen_log"] = [{"time": 1228}, {"time": 1231, "entityleft": True}]
        result, = analyze_fights(raw, 129, HEROES)
        sentry = next(i for i in result["items"] if i["key"] == "ward_sentry")
        self.assertEqual(sentry["status"], "used")
        self.assertIn("20:28", sentry["evidence"])
        self.assertNotIn("20:31", sentry["evidence"])

    def test_role_specific_active_button_advice_requires_a_valid_zero_uses_log(self):
        raw = match()
        f = raw["teamfights"][0]["players"]
        f[0]["ability_uses"] = {"rubick_telekinesis": 1}
        f[1]["ability_uses"] = {}
        actor(raw, 129).update(position_est=3, purchase_log=[{"key": "black_king_bar", "time": 1000}])
        result, = analyze_fights(raw, 129, HEROES)
        self.assertIn("до выхода на дистанцию Telekinesis", result["action"])
        f[6]["item_uses"] = None
        result, = analyze_fights(raw, 129, HEROES)
        self.assertNotIn("Если BKB была", result["action"])
        f[0]["ability_uses"] = {"lina_dragon_slave": 1}
        f[6]["item_uses"] = {}
        actor(raw, 129).update(position_est=4, purchase_log=[{"key": "glimmer_cape", "time": 1000}])
        result, = analyze_fights(raw, 129, HEROES)
        self.assertIn("Glimmer Cape был готов", result["action"])
        self.assertIn("барьер", result["action"])
        actor(raw, 129)["purchase_log"] = [{"key": "glimmer_cape", "time": 1800}]
        result, = analyze_fights(raw, 129, HEROES)
        self.assertIn("появилась после этой драки", result["action"])
        self.assertIn("выход из тумана", result["action"])
        self.assertIn("золото и доставка", result["check"])

    def test_legacy_internal_name_shows_the_actual_spell(self):
        self.assertEqual(_spell_name("techies_suicide", "techies"), "Blast Off!")
        self.assertEqual(_spell_name("lion_voodoo", "lion"), "Hex")
        self.assertEqual(_spell_name("future_hero_new_spell", "future_hero"), "New Spell")

    def test_exact_personal_event_and_target_counter_do_not_time_the_spell(self):
        raw = match()
        actors = raw["teamfights"][0]["players"]
        actors[1]["ability_targets"] = {"zuus_lightning_bolt": {"npc_dota_hero_lion": 1}}
        actors[6]["item_uses"] = {"glimmer_cape": 1}
        actor(raw, 1)["kills_log"] = [{"time": 1219, "key": "npc_dota_hero_lion"}]
        fight, = analyze_fights(raw, 129, HEROES)
        self.assertEqual((fight["start"], fight["end"], fight["anchor"], fight["anchor_kind"]),
                         (1200, 1240, 1219, "death"))
        self.assertEqual(fight["events"], [{"time": 1219, "kind": "death", "text": "Твоя смерть"}])
        first = fight["threats"][0]
        self.assertEqual((first["hero_id"], first["ability"], first["casts"], first["on_player"]),
                         (22, "Lightning Bolt", 2, 1))
        self.assertIn("×2", fight["observations"][0])
        self.assertIn("Hex", " ".join(fight["observations"]))
        glimmer = next(i for i in fight["items"] if i["key"] == "glimmer_cape")
        self.assertEqual((glimmer["status"], glimmer["hero_id"], glimmer["ability"]), ("used", 22, "Lightning Bolt"))
        self.assertIn("за драку", " ".join(fight["observations"]))

    def test_evidence_states_before_during_after_and_passive(self):
        raw = match()
        actor(raw, 129)["purchase_log"] = [
            {"time": 400, "key": "recipe_sphere"}, {"time": 1000, "key": "sphere"},
            {"time": 1200, "key": "black_king_bar"}, {"time": 1241, "key": "glimmer_cape"}]
        fight, = analyze_fights(raw, 129, HEROES)
        items = {i["key"]: i for i in fight["items"]}
        self.assertEqual(items["sphere"]["status"], "prior_purchase")
        self.assertIn("пассивного", items["sphere"]["evidence"])
        self.assertEqual(items["black_king_bar"]["status"], "during_purchase")
        self.assertEqual(items["glimmer_cape"]["status"], "later_purchase")
        self.assertIn("20:41", items["glimmer_cape"]["evidence"])
        self.assertEqual(items["sphere"]["category"], "pierce")
        self.assertEqual(items["black_king_bar"]["category"], "magic_burst")
        self.assertIn("позиции", fight["action"])

    def test_recipe_only_is_not_ownership_and_undated_inventory_is_not_a_missing_buy(self):
        raw = match()
        actor(raw, 129).update(position_est=1, purchase_log=[{"time": 400, "key": "recipe_sphere"}],
                               item_0=116)
        result, = analyze_fights(raw, 129, HEROES)
        items = {i["key"]: i for i in result["items"]}
        self.assertEqual(items["sphere"]["status"], "unseen")
        self.assertEqual(items["black_king_bar"]["status"], "unknown_timing")
        actor(raw, 129)["purchase_log"] = None
        result, = analyze_fights(raw, 129, HEROES)
        self.assertTrue(all(i["status"] == "unknown_timing" for i in result["items"]))

    def test_upgrades_do_not_get_suggested_again_and_missing_log_is_distinguished_from_zero(self):
        raw = match()
        actor(raw, 129).update(position_est=4, purchase_log=[{"time": 450, "key": "hurricane_pike"}])
        fight = raw["teamfights"][0]["players"]
        fight[2]["ability_uses"] = {"skywrath_mage_mystic_flare": 1}
        fight[6]["item_uses"] = None
        result, = analyze_fights(raw, 129, HEROES)
        force = next(i for i in result["items"] if i["key"] == "force_staff")
        self.assertEqual(force["status"], "prior_purchase")
        self.assertIn("Hurricane Pike", force["evidence"])
        self.assertIn("Журнал применений", force["evidence"])
        fight[6]["item_uses"] = {"hurricane_pike": 1}
        result, = analyze_fights(raw, 129, HEROES)
        force = next(i for i in result["items"] if i["key"] == "force_staff")
        self.assertEqual(force["status"], "used")
        self.assertIn("Hurricane Pike ×1", force["evidence"])

    def test_no_cast_uses_no_whole_game_or_opposite_side_attribution(self):
        raw = match()
        actor(raw, 0)["ability_uses"] = {"legion_commander_duel": 20}
        f = raw["teamfights"][0]["players"]
        f[0]["ability_uses"] = {}
        f[1]["ability_uses"] = None
        f[5]["ability_uses"] = {"rubick_telekinesis": 2}
        result, = analyze_fights(raw, 129, HEROES)
        self.assertEqual(result["threats"], [])
        self.assertEqual(result["items"], [])
        self.assertEqual(analyze_fights(raw, 132, HEROES), [])
        actor(raw, 129)["deaths_log"] = []
        result, = analyze_fights(raw, 129, HEROES)
        self.assertEqual((result["anchor"], result["anchor_kind"]), (1200, "interval"))

    def test_roles_have_different_practice_and_no_bkb_against_a_piercing_ultimate(self):
        raw = match()
        raw["teamfights"][0]["players"][1]["ability_uses"] = {}
        advice = [analyze_fights(raw, 129, HEROES, position=i)[0] for i in range(1, 6)]
        self.assertEqual(len({f["action"] for f in advice}), 5)
        self.assertEqual(len({f["check"] for f in advice}), 5)
        self.assertFalse(any(i["key"] == "black_king_bar" for f in advice for i in f["items"]))
        self.assertTrue(any(i["key"] == "sphere" for i in advice[0]["items"]))
        self.assertEqual(advice[-1]["items"], [])
        self.assertIn("Duel", advice[-1]["action"])

    def test_earlier_activation_and_team_detection_have_qualified_evidence(self):
        raw = match()
        prev = deepcopy(raw["teamfights"][0])
        prev.update(start=1000, end=1030)
        prev["players"][6]["item_uses"] = {"glimmer_cape": 1}
        raw["teamfights"].append(prev)
        f = raw["teamfights"][0]["players"]
        f[1]["item_uses"] = {"silver_edge": 1}
        f[5]["item_uses"] = {"dust": 1}
        _, second = analyze_fights(raw, 129, HEROES)
        items = {i["key"]: i for i in second["items"]}
        self.assertEqual(items["glimmer_cape"]["status"], "unknown_timing")
        self.assertIn("17:10", items["glimmer_cape"]["evidence"])
        self.assertIn("Ursa", items["dust"]["evidence"])
        self.assertEqual(items["dust"]["threat_hero"], "Zeus")
