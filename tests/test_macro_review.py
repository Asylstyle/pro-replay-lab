from copy import deepcopy
import unittest

from pro_replay.fight_review import SLOTS
from pro_replay.macro_review import macro_review, render_macro


class MacroReviewTests(unittest.TestCase):
    def setUp(self):
        self.heroes = {i + 1: {"localized_name": f"Hero {i}", "name": f"unit{i}"} for i in range(10)}
        players = [{"hero_id": i+1, "player_slot": s, "item_uses": {}, "purchase_log": []} for i,s in enumerate(SLOTS)]
        players[0].update(purchase_log=[{"time": 50, "key": "smoke_of_deceit"}], item_uses={"smoke_of_deceit": 1})
        self.raw = {"match_id": 100, "duration": 130, "start_time": 1789600000, "players": players,
                    "teamfights": [{"start": 95, "end": 102}], "objectives": [
                        {"type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 2, "time": 128},
                        {"type": "building_kill", "key": "npc_dota_badguys_tower1_top", "time": 108}]}
        states = {str(s): {"hero_id": i+1, "alive": True, "x": 0 if s<128 else 5000,
                           "y": 0, "visible_to": {"2": s == 128}, "buttons": {}, "unit_state": 0, "mana": 50}
                  for i,s in enumerate(SLOTS)}
        states["0"]["buttons"]["smoke_of_deceit"] = {"kind": "item", "slot": 2, "active": True,
            "charges": 1, "mana_cost": 0, "cooldown": 0, "channeling": False}
        self.timeline = {"format": "pro-replay-truth-v1", "match_id": 100, "frames": [
            {"time": 60, "tick": 1800, "heroes": states}], "events": [
            {"kind": "cast", "time": 85, "tick": 2500, "actor_slot": 0, "button_key": "smoke_of_deceit"},
            {"kind": "cast", "time": 94, "tick": 2800, "actor_slot": 129, "button_key": "smoke_of_deceit"}]}

    def review(self, timeline):
        return macro_review(self.raw, 0, self.heroes, timeline)

    def test_real_cast_and_next_event_are_team_relative(self):
        view = self.review(self.timeline)
        self.assertEqual(view["casts"], [(85, "Hero 0", (95, "Начало размеченной драки"))])
        self.assertEqual([w["time"] for w in view["windows"]], [60])
        self.assertEqual(view["uses"], 1)
        self.assertEqual([p[0] for p in view["plans"]], [78, 98])
        self.assertIn("1:25", render_macro(view))
        dire = macro_review(self.raw, 128, self.heroes, self.timeline)
        self.assertEqual(dire["casts"][0][0:2], (94, "Hero 6"))
        self.assertEqual(dire["buys"], [])

    def test_missing_charges_fog_cooldown_and_backpack_suppress_recommendation(self):
        for mode in ("charges", "backpack", "cooldown", "control", "hidden", "near", "patch", "hero_mismatch"):
            with self.subTest(mode=mode):
                t = deepcopy(self.timeline)
                st = t["frames"][0]["heroes"]
                item = st["0"]["buttons"]["smoke_of_deceit"]
                if mode == "charges": del item["charges"]
                if mode == "backpack": item["slot"] = 6
                if mode == "cooldown": item["cooldown"] = 4
                if mode == "control": st["0"]["unit_state"] = 1 << 5
                if mode == "hidden": st["128"]["visible_to"]["2"] = False
                if mode == "near": st["128"]["x"] = 300
                if mode == "patch": t["patch_version"] = "unknown"
                if mode == "hero_mismatch": st["128"]["hero_id"] = 999
                self.assertFalse(self.review(t)["windows"])

    def test_purchase_never_creates_use_and_hidden_enemy_positions_cannot_change_call(self):
        for timeline in (None, dict(self.timeline, match_id=17)):
            view = self.review(timeline)
            self.assertEqual(view["buys"], [(50, "Hero 0")])
            self.assertFalse(view["casts"] or view["windows"])
        changed = deepcopy(self.timeline)
        changed["frames"][0]["heroes"]["129"]["x"] = 8
        self.assertEqual(self.review(changed)["windows"], self.review(self.timeline)["windows"])
        self.assertIn("Roshan", render_macro(self.review(None)))
