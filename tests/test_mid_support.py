from copy import deepcopy
from unittest import TestCase

from pro_replay.mid_exchange import exchange_card, select_decisions
from pro_replay.mid_support import SupportTracker, annotate_support_casts, render_support


def unit(hero, x, **overrides):
    return {"hero_id": hero, "x": x, "y": 0, "alive": True, "unit_state": 0, "buttons": {},
            "visible_to": {"2": True, "3": True}, **overrides}


class SupportTests(TestCase):
    def setUp(self):
        self.players = [{"hero_id": 26, "player_slot": 2, "position_est": 5},
                        {"hero_id": 22, "player_slot": 0, "position_est": 2},
                        {"hero_id": 34, "player_slot": 128, "position_est": 2}]
        self.frames = []
        button = {"kind": "ability", "level": 1, "active": True, "cooldown": 0, "mana_cost": 110}
        for t, x in enumerate((900, 760, 640, 515), start=100):
            ally = unit(26, x, mana=111, buttons={"lion_voodoo": dict(button)})
            self.frames.append({"time": t, "game_time": t+.03, "tick": 30*t,
                                "heroes": {"2": ally, "128": unit(34, 0)}})

    def review(self, frames=None, clean_range=True):
        tracker = SupportTracker({"players": self.players}, 0, 128, 34)
        for f in self.frames if frames is None else frames:
            tracker.observe(f)
        return tracker.review(clean_range)

    def test_direct_hex_at_current_distance_requires_approach_and_resources(self):
        check = self.review()
        self.assertEqual((check["state"], check["hero"], check["role"]), ("ready", "Lion", 5))
        self.assertEqual(check["movement"], {"closing": True, "start": 100, "before": 900})
        b = check["button"]
        self.assertEqual((check["distance"], b["reach"], b["duration"], b["arrival"]), (515, 575, 2, 0))
        for label, update in (("mana", {"mana": 109}), ("silenced", {"unit_state": 1 << 3})):
            with self.subTest(label=label):
                frames = deepcopy(self.frames)
                frames[-1]["heroes"]["2"].update(update)
                self.assertEqual(self.review(frames)["state"], "blocked")
        for update in ({"cooldown": .01}, {"level": 0}, {"charges": 0}, {"in_phase": True}):
            with self.subTest(update=update):
                frames = deepcopy(self.frames)
                frames[-1]["heroes"]["2"]["buttons"]["lion_voodoo"].update(update)
                self.assertEqual(self.review(frames)["state"], "blocked")

    def test_approaching_is_distinct_from_already_in_range(self):
        # Earlier observations contain no future trajectory.
        self.assertNotEqual(self.review(self.frames[:3])["state"], "ready")
        for f in self.frames:
            f["heroes"]["2"]["x"] += 70
        self.assertEqual(self.review()["state"], "approaching")
        self.frames[-1]["heroes"]["2"]["buttons"]["lion_voodoo"]["cast_range"] = 620
        self.assertEqual(self.review(clean_range=False)["state"], "ready")
        del self.frames[-1]["heroes"]["2"]["buttons"]["lion_voodoo"]["cast_range"]
        self.assertEqual(self.review(clean_range=False)["state"], "unknown")
        # A stationary ally and a rival walking into that ally's range.
        for frame, x in zip(self.frames, (-900, -760, -640, -500)):
            frame["heroes"]["2"]["x"] = 0
            frame["heroes"]["128"]["x"] = x
        self.assertEqual(self.review()["state"], "in_range")

    def test_continuity_identity_visibility_and_target_defence(self):
        for label, edit in (
            ("invisible past target", lambda f: f[1]["heroes"]["128"]["visible_to"].update({"2": False})),
            ("hidden current target", lambda f: f[-1]["heroes"]["128"]["visible_to"].update({"2": False})),
            ("out of game", lambda f: f[-1]["heroes"]["128"].update(unit_state=1 << 37)),
            ("illusion or wrong slot", lambda f: f[0]["heroes"]["2"].update(hero_id=99)),
            ("dead support", lambda f: f[-1]["heroes"]["2"].update(alive=False)),
            ("missing frame", lambda f: f.pop(1)),
            ("nonadvancing tick", lambda f: f[-1].update(tick=f[-2]["tick"])),
            ("clock gap", lambda f: f[-1].update(game_time=105.03)),
            ("jump instead of approach", lambda f: f[0]["heroes"]["2"].update(x=1500)),
            ("spell block", lambda f: f[-1]["heroes"]["128"]["buttons"].update(
                sphere={"kind": "item", "slot": 0})),
        ):
            with self.subTest(label=label):
                frames = deepcopy(self.frames)
                edit(frames)
                self.assertNotEqual(self.review(frames)["state"], "ready")

    def test_support_identity_requires_a_unique_allied_role_estimate(self):
        for change in ({"position_est": 1}, {"position_est": None}, {"player_slot": 130}, {"player_slot": 0}):
            self.players[0] = {"hero_id": 26, "player_slot": 2, "position_est": 5, **change}
            self.assertEqual(self.review()["state"], "unknown")
        self.players[0] = {"hero_id": 26, "player_slot": 2, "position_est": 5}
        self.players.append({"hero_id": 51, "player_slot": 3, "position_est": 5})
        self.assertEqual(self.review()["state"], "unknown")

    def test_solo_and_coordinated_orders_are_separate_and_use_past_frames(self):
        own = {"key": "nuke", "spell": "Nuke", "damage": 150, "arrival": .5, "cast_point": .1}
        frame = {"time": 103, "tick": 3090, "own_hp": 650, "own_max": 650, "enemy_hp": 900,
                 "enemy_max": 1000, "hero": [own], "creeps": [], "support": self.review(),
                 "reply": {"best": None, "controls": [], "unknown": [], "blocked": []},
                 "tower_nearby": False, "extra_hero": False}
        before = exchange_card(frame)
        self.assertTrue(before["coordinated"])
        self.assertEqual(before["action"], "trade")
        frame["support"]["target_disabled"] = True
        self.assertFalse(exchange_card(frame)["coordinated"])
        self.assertIn("предыдущего контроля", render_support(exchange_card(frame), "Tinker"))
        frame["support"] = self.review(self.frames[:1])
        self.assertEqual(exchange_card(frame)["action"], before["action"])
        self.assertFalse(exchange_card(frame)["coordinated"])
        frame["support"] = self.review()
        for field, value in (("ready", False), ("arrival", None), ("arrival", 2.1)):
            with self.subTest(field=field):
                altered = deepcopy(frame)
                altered["hero"][0][field] = value
                self.assertFalse(exchange_card(altered)["coordinated"])
        frame["extra_hero"] = True
        after = exchange_card(frame)
        self.assertFalse(after["coordinated"])
        ordinary = {**before, "time": 20, "support": {}, "coordinated": False}
        self.assertIn(after, select_decisions([ordinary, after]))
        cast = {"kind": "cast", "actor_slot": 2, "target_slot": 128, "time": 103,
                "tick": 3092, "button_key": "lion_voodoo"}
        annotate_support_casts([after], [dict(cast,tick=3089), dict(cast,target_slot=129),
                                         dict(cast, time=105, game_time=105.2)], 128)
        self.assertNotIn("observed", after["support"])
        annotate_support_casts([after], [dict(cast, game_time=103.933)], 128)
        self.assertIn("1:43,93 журнал фиксирует применение Hex", render_support(after, "Tinker"))
        self.assertNotIn("Вариант вдвоём", render_support(after, "Tinker"))
