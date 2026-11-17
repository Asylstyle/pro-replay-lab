import unittest

from pro_replay.coach_report import battle_timeline
from pro_replay.coaching import tactical_card
from pro_replay.replay_choices import replay_choices


def sample():
    heroes = {14: {"localized_name": "Pudge"}, 114: {"localized_name": "Monkey King"},
              72: {"localized_name": "Gyrocopter"}, 88: {"localized_name": "Nyx Assassin"}}
    slots = {131: 14, 132: 114, 0: 72, 1: 88}
    raw = {"match_id": 123, "players": [{"player_slot": slot, "hero_id": hid} for slot, hid in slots.items()]}
    buttons = {"pudge_dismember": {"kind": "ability", "level": 2, "active": True},
               "glimmer_cape": {"kind": "item", "slot": 4, "active": True},
               "item_sheepstick": {"kind": "item", "slot": 7, "active": True},
               "ogre_magi_fireblast": {"kind": "ability", "level": 0, "active": False}}
    frames = [{"time": t, "tick": t * 30, "heroes": {
        str(slot): {"hero_id": hid, "alive": True, "buttons": buttons if slot == 131 else {}}
        for slot, hid in slots.items()}} for t in range(90, 111)]
    events = [{"time": 100, "tick": 3014, "kind": "death", "actor_slot": 0, "target_slot": 132}]
    timeline = {"match_id": 123, "events": events, "frame_window": lambda lo, hi: [f for f in frames if lo <= f["time"] <= hi]}
    return raw, heroes, timeline, frames


class ReplayChoiceTests(unittest.TestCase):
    def test_unparsed_game_offers_specific_buttons_for_verification_with_renderable_fight(self):
        raw, heroes, timeline, _ = sample()
        fights, choices = replay_choices(timeline, raw, 131, heroes, [], 4)
        self.assertEqual(len(fights), 1)
        self.assertEqual([(d["button_key"], d["target"], d["recipient_side"]) for _, d in choices],
                         [("item_glimmer_cape", "Monkey King", "ally"), ("pudge_dismember", "Gyrocopter", "enemy")])
        for _, decision in choices:
            self.assertEqual((decision["event_actor_slot"], decision["event_target_slot"]), (0, 132))
            self.assertTrue(decision["requires_replay_verification"])
            self.assertEqual(decision["focus_slot"], 0)
        session = {"fights": fights, "selected_fight": fights[0], "focus": "auto", "cards": [], "position": 4}
        self.assertIn('id="fight-92-100"', battle_timeline(session))
        self.assertIn("Gyrocopter", tactical_card(fights[0], {"hero_name": "Pudge"})["evidence"])

    def test_suppress_matching_and_unattributed_casts_but_allow_other_known_target(self):
        raw, heroes, timeline, _ = sample()
        cast = {"kind": "cast", "time": 98, "actor_slot": 131, "target_slot": 0, "button_key": "pudge_dismember"}
        timeline["events"] += [cast, {"kind": "cast", "time": 95, "actor_slot": 131, "button_key": "glimmer_cape"}]
        self.assertEqual(replay_choices(timeline, raw, 131, heroes, [], 4), ([], []))
        cast["target_slot"] = 1
        _, choices = replay_choices(timeline, raw, 131, heroes, [], 4)
        self.assertEqual([d["button_key"] for _, d in choices], ["pudge_dismember"])

    def test_only_frames_before_death_with_matching_roster_and_living_actor_qualify(self):
        raw, heroes, timeline, frames = sample()
        for f in frames:
            if f["time"] < 100:
                f["heroes"]["131"]["alive"] = False
            if f["time"] == 100:
                f["tick"] = 3015
        self.assertEqual(replay_choices(timeline, raw, 131, heroes, [], 4), ([], []))
        frames[9]["heroes"]["131"]["alive"] = True
        frames[9]["heroes"]["0"]["hero_id"] = 99
        self.assertEqual(replay_choices(timeline, raw, 131, heroes, [], 4), ([], []))
        timeline["match_id"] = 999
        self.assertEqual(replay_choices(timeline, raw, 131, heroes, [], 4), ([], []))

    def test_aggregate_and_new_windows_have_consistent_bounds_and_no_duplicate_fights(self):
        raw, heroes, timeline, _ = sample()
        parsed = {"start": 80, "end": 100}
        timeline["events"].extend([{"time": 103, "tick": 3100, "kind": "death", "actor_slot": 1, "target_slot": 131},
                                   {"time": 108, "tick": 3250, "kind": "death", "actor_slot": 0, "target_slot": 132},
                                   {"time": 109, "tick": 3275, "kind": "death", "actor_slot": 131, "target_slot": 0}])
        fights, choices = replay_choices(timeline, raw, 131, heroes, [parsed], 4)
        self.assertEqual([(f["start"], f["end"]) for f in fights], [(101, 108)])
        self.assertEqual([(e["time"], e["kind"]) for e in fights[0]["events"]], [(103, "death"), (108, "ally_death")])
        self.assertEqual({(d["start"], d["end"]) for _, d in choices}, {(80, 100), (101, 108)})
        self.assertEqual(parsed, {"start": 80, "end": 100})
