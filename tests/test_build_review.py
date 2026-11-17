import json
import unittest

from pro_replay.build_review import SLOTS, extract_build, render_build


def fight(start, end, my_uses, opposing_uses):
    actors = [{"ability_uses": {}, "item_uses": {}} for _ in SLOTS]
    actors[6]["item_uses"] = my_uses  # Selected player is Dire slot 129.
    actors[0]["ability_uses"] = opposing_uses
    return {"start": start, "end": end, "players": actors}


def example():
    heroes = [49, 2, 145, 32, 112, 69, 26, 16, 68, 96]
    players = [{"player_slot": s, "hero_id": h, "purchase_log": [], "item_uses": {},
                **{f"item_{i}": 0 for i in range(6)}} for s, h in zip(SLOTS, heroes)]
    players[6].update(position_est=5, net_worth=9000, purchase_log=[
        {"key": "recipe_sphere", "time": 50}, {"key": "sphere", "time": 105},
        {"key": "sphere", "time": 140}, {"key": "blink", "time": 55}],
        item_0=123, item_1=1466, item_2=999999)
    players[5]["purchase_log"] = [{"key": "dust", "time": 100}]
    players[2]["hero_healing"] = 4000
    players[3]["item_0"] = 139
    return {"players": list(reversed(players)), "teamfights": [
        fight(70, 85, {}, {"dragon_knight_dragon_tail": 1}),
        fight(100, 110, {"sphere": 1}, {"dragon_knight_dragon_tail": 1}),
        fight(115, 125, {"sphere": 1}, {"dragon_knight_dragon_tail": 3}),
        fight(130, 135, None, {})]}


class BuildReviewTests(unittest.TestCase):
    def test_draft_uses_correct_side_inventory_and_exact_item_key(self):
        review = extract_build(example(), 129, {26: {"localized_name": "Lion"}})
        self.assertEqual(review["hero"], "Lion")
        self.assertEqual(len(review["enemies"]), 5)
        self.assertTrue(review["draft_complete"])
        self.assertEqual(review["inventory"], ["sphere", "gungir", "ID 999999"])
        items = {i["key"]: i for i in review["items"]}
        self.assertEqual(items["sphere"]["purchase"], 105)
        self.assertIsNone(items["gungir"]["purchase"])
        self.assertEqual(items["sphere"]["after_casts"], 1)
        self.assertEqual(items["sphere"]["after_windows"], 1)
        self.assertEqual(sum(i["casts"] for i in items["sphere"]["intervals"]), 2)
        findings = {f["category"]: f for f in review["findings"]}
        control = findings["target"]
        self.assertEqual(control["personal"], ["sphere"])
        self.assertEqual(control["checks"][0]["threats"], ["Dragon Tail ×3"])
        self.assertEqual(control["timing"][0], {"item": "sphere", "purchase": 105, "before": 1, "overlap": 1, "after": 1})
        self.assertFalse(findings["vision"]["personal"])
        self.assertIn("Dust of Appearance", findings["vision"]["team"][0])
        self.assertTrue(any("Doom" in s for s in findings["healing"]["native"]))
        self.assertEqual(findings["area"]["personal"], [])
        html = render_build(json.loads(json.dumps(review)), patch_version="7.40")
        for word in ("Герой #49", "Gleipnir", "Dragon Tail ×3", "позиция 5", "7.40", "Monkey King Bar",
                     "Справочные цены", "количество пассивных блоков неизвестно", "Cold Embrace"):
            self.assertIn(word, html)

    def test_unlogged_buy_and_passive_are_distinct_from_active_evidence(self):
        raw = example()
        selected = next(p for p in raw["players"] if p["player_slot"] == 129)
        selected["purchase_log"] = None
        selected["item_0"] = 116   # BKB can help against ordinary targeted control.
        raw["teamfights"] = [fight(100, 120, {}, {"dragon_knight_dragon_tail": 1})]
        review = extract_build(raw, 129, {})
        self.assertFalse(review["log_known"])
        findings = {f["category"]: f for f in review["findings"]}
        self.assertEqual(findings["target"]["personal"], ["black_king_bar"])
        self.assertEqual(findings["pierce"]["personal"], [])
        bkb = next(i for i in review["items"] if i["key"] == "black_king_bar")
        self.assertIsNone(bkb["purchase"])
        self.assertEqual(bkb["after_windows"], 0)

    def test_missing_rosters_fights_unknown_inventory_and_escape(self):
        raw = {"players": [{"player_slot": 0, "hero_id": 1}], "teamfights": [
            {"start": 3, "end": 2, "players": [{} for _ in SLOTS]}, {"players": []}]}
        result = extract_build(raw, 0, {1: {"localized_name": "<x>"}})
        self.assertFalse(result["draft_complete"])
        self.assertFalse(result["slots_known"])
        self.assertEqual(result["fights"], 0)
        self.assertIn("&lt;x&gt;", render_build(result))
        self.assertNotIn("<x>", render_build(result))
        self.assertEqual(extract_build(raw, 128, {}), {})
        self.assertIn("нужны", render_build(None))

    def test_direct_enemy_unit_damage_and_personal_blink_control(self):
        raw = example()
        selected = next(p for p in raw["players"] if p["player_slot"] == 129)
        selected["damage_taken"] = {"npc_dota_hero_dragon_knight": 1360, "npc_dota_hero_lion": 150,
                                    "npc_dota_hero_dragon_knight_illusion": 900, "npc_dota_creep": 800}
        f = raw["teamfights"][0]["players"][6]
        f["item_uses"] = {"blink": 1}
        f["ability_uses"] = {"lion_voodoo": 2}
        heroes = {49: {"name": "npc_dota_hero_dragon_knight", "localized_name": "Dragon Knight"},
                  26: {"name": "npc_dota_hero_lion", "localized_name": "Lion"}}
        review = extract_build(raw, 129, heroes)
        self.assertEqual(review["incoming"], [{"hero": "Dragon Knight", "damage": 1360}])
        self.assertEqual(review["blink_combo"]["windows"], [{"start": 70, "end": 85, "blinks": 1, "spells": 2}])
