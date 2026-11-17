from copy import deepcopy
import json
from pathlib import Path
import unittest

from pro_replay.replay_verification import CASTS, ReplayVerifier


def hero(**changes):
    unit = {"alive": True, "unit_state": 0, "mana": 200, "x": 0, "y": 0,
            "visible_to": {"2": True, "3": False}, "hero_id": 14,
            "buttons": {"pudge_dismember": {"active": True, "level": 1, "cooldown": 0,
                                           "mana_cost": 100, "slot": 3, "kind": "ability"}}}
    unit.update(changes)
    return unit


def sample(time=98):
    return {"time": time, "game_time": time + .03, "tick": time * 30,
            "heroes": {"0": hero(), "128": hero(x=100, hero_id=72)}}


def selection(**changes):
    option = {"button_key": "pudge_dismember", "target_slot": 128, "focus_slot": 128,
              "start": 90, "end": 110, "time": 100, "kind": "focus",
              "event_kind": "death", "event_actor_slot": 128, "event_target_slot": 0}
    option.update(changes)
    return option


def reviewer(frames, events=(), **changes):
    settings = {"match_id": 123, "start_time": CASTS["from"] + 1,
                "hero_ids": [14, 26, 27, 72, 70, 25, 22, 24, 23, 20], "game_mode": 22}
    settings.update(changes)
    return ReplayVerifier({"format": "pro-replay-truth-v1", "match_id": 123,
                           "frames": frames, "events": list(events)}, **settings)


class ReplayVerificationTests(unittest.TestCase):
    def test_real_valve_demo_snapshots_cover_ready_cooldown_and_team_fog(self):
        # The fixture retains actual Source 2 frames and their replay SHA256.
        # Steam's byte-exact demo source URLs are recorded alongside the cases.
        fixture = Path(__file__).with_name("fixtures") / "replay-verification-cases.json"
        for case in json.loads(fixture.read_text(encoding="utf8"))["cases"]:
            with self.subTest(match_id=case["match_id"], event=case["decision"]["time"]):
                truth = {"format":"pro-replay-truth-v1", "match_id":case["match_id"],
                         "frames":case["frames"], "events":case["events"]}
                v = ReplayVerifier(truth, match_id=case["match_id"], start_time=case["start_time"],
                                   game_mode=case["game_mode"], hero_ids=case["hero_ids"])
                result = v.verify(case["slot"], case["decision"])
                self.assertEqual((result["status"], result["time"]), (case["expected_status"], case["expected_time"]))
                if result["time"] == 789:
                    checks = {c["key"]:c["state"] for c in result["checks"]}
                    self.assertEqual(checks["vision"], "no")
                    self.assertEqual((checks["range"], checks["cooldown"], checks["mana"]), ("yes", "yes", "yes"))

    def test_ready_visible_in_range_uses_one_measured_frame(self):
        f = sample()
        result = reviewer([f]).verify(0, selection())
        self.assertEqual(result["status"], "verified")
        self.assertEqual((result["time"], result["tick"], result["game_time"]), (98, 2940, 98.03))
        self.assertTrue(all(c["state"] == "yes" for c in result["checks"]))

    def test_never_splices_ready_visible_and_in_range_across_frames(self):
        a, b, c = sample(96), sample(97), sample(98)
        a["heroes"]["0"]["buttons"]["pudge_dismember"]["cooldown"] = 4
        b["heroes"]["128"]["visible_to"]["2"] = False
        c["heroes"]["128"]["x"] = 7000
        c["heroes"]["128"]["hull_radius"] = 24
        c["heroes"]["0"]["hull_radius"] = 24
        c["heroes"]["0"]["buttons"]["pudge_dismember"]["cast_range"] = 200
        result = reviewer([a, b, c]).verify(0, selection())
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any(x["state"] == "no" for x in result["checks"]))

    def test_missing_values_never_become_zero_or_visible(self):
        for unit, key in (("0", "mana"), ("0", "unit_state"), ("128", "visible_to"),
                          ("128", "alive"), ("128", "x")):
            f = sample()
            del f["heroes"][unit][key]
            self.assertEqual(reviewer([f]).verify(0, selection())["status"], "unknown", key)
        for field in ("active", "cooldown", "level", "mana_cost"):
            f = sample()
            del f["heroes"]["0"]["buttons"]["pudge_dismember"][field]
            self.assertEqual(reviewer([f]).verify(0, selection())["status"], "unknown", field)

    def test_only_own_team_visibility_can_pass_and_items_ignore_spell_silence(self):
        f = sample()
        actor = f["heroes"]["0"]
        actor["unit_state"] = 1 << 3
        actor["buttons"] = {"glimmer_cape": {"active": True, "level": 1, "cooldown": 0,
                                             "mana_cost": 125, "slot": 1, "kind": "item"}}
        f["heroes"]["1"] = hero(x=200, hero_id=26)
        d = selection(button_key="item_glimmer_cape", target_slot=1, kind="protect")
        self.assertEqual(reviewer([f]).verify(0, d)["status"], "verified")
        actor["buttons"]["glimmer_cape"]["slot"] = 6
        self.assertEqual(reviewer([f]).verify(0, d)["status"], "blocked")
        actor["buttons"]["glimmer_cape"]["slot"] = 1
        actor["unit_state"] = 1 << 4
        self.assertEqual(reviewer([f]).verify(0, d)["status"], "blocked")
        actor["unit_state"] = 0
        f["heroes"]["128"]["visible_to"] = {"2": False, "3": True}
        self.assertEqual(reviewer([f]).verify(0, d)["status"], "blocked")

    def test_range_is_unknown_for_other_patch_and_measured_effective_range_is_used(self):
        f = sample()
        old = reviewer([f], start_time=CASTS["from"] - 1)
        self.assertEqual(old.verify(0, selection())["status"], "unknown")
        unknown = reviewer([f], start_time=CASTS["through"] + 20, patch_version="7.42")
        self.assertEqual(unknown.verify(0, selection())["status"], "unknown")
        current = reviewer([f], start_time=CASTS["through"] + 20, patch_version="7.41f")
        self.assertEqual(current.verify(0, selection())["status"], "verified")
        f["heroes"]["0"]["buttons"]["pudge_dismember"]["cast_range"] = 200
        self.assertEqual(old.verify(0, selection())["status"], "verified")
        f["heroes"]["128"]["x"] = 300
        f["heroes"]["128"]["hull_radius"] = 24
        f["heroes"]["0"]["hull_radius"] = 24
        self.assertEqual(old.verify(0, selection())["status"], "blocked")

    def test_immunity_control_and_non_unit_geometry(self):
        f = sample()
        f["heroes"]["0"]["unit_state"] = 1 << 5
        self.assertEqual(reviewer([f]).verify(0, selection())["status"], "blocked")
        f["heroes"]["0"]["unit_state"] = 0
        f["heroes"]["128"]["unit_state"] = 1 << 56
        self.assertEqual(reviewer([f]).verify(0, selection())["status"], "verified")
        b = f["heroes"]["0"]["buttons"].pop("pudge_dismember")
        f["heroes"]["0"]["buttons"]["lion_voodoo"] = b
        self.assertEqual(reviewer([f]).verify(0, selection(button_key="lion_voodoo"))["status"], "blocked")
        f["heroes"]["128"]["unit_state"] = 0
        f["heroes"]["0"]["buttons"]["mars_spear"] = b
        b["cast_range"] = 1200
        self.assertEqual(reviewer([f]).verify(0, selection(button_key="mars_spear"))["status"], "unknown")

    def test_actual_cast_to_target_suppresses_suggestion_on_all_sources(self):
        f = sample()
        cast = {"kind": "cast", "time": 99, "game_time": 99.2, "tick": 2976,
                "actor_slot": 0, "target_slot": 128, "button_key": "pudge_dismember"}
        result = reviewer([f], [cast]).verify(0, selection())
        self.assertTrue(result["already_used"])
        cast["target_slot"] = 129
        self.assertEqual(reviewer([f], [cast]).verify(0, selection())["status"], "verified")

    def test_fractional_death_and_castpoint_exclude_late_frames(self):
        event = {"kind": "death", "time": 99, "game_time": 99.4, "tick": 2982, "actor_slot": 128, "target_slot": 0}
        early, late = sample(98), sample(99)
        late["game_time"], late["tick"] = 99.2, 2976
        result = reviewer([early, late], [event]).verify(0, selection())
        self.assertEqual(result["time"], 98)
        self.assertEqual(reviewer([late], [event]).verify(0, selection())["status"], "unknown")
        late["game_time"], late["tick"] = 99.03, 2971
        self.assertEqual(reviewer([late], [event]).verify(0, selection())["status"], "verified")
        late["game_time"], late["tick"] = 99.8, 2994
        self.assertEqual(reviewer([late], [event]).verify(0, selection())["status"], "unknown")

    def test_interrupt_requires_observed_channel_in_same_frame(self):
        f = sample()
        f["heroes"]["128"]["buttons"] = {"enigma_black_hole": {"channeling": True, "channel_start":96.5}}
        following = deepcopy(f)
        following.update(time=99, tick=2970, game_time=99.03)
        d = selection(kind="interrupt", threat_key="enigma_black_hole")
        self.assertEqual(reviewer([f, following]).verify(0, d)["status"], "verified")
        self.assertEqual(reviewer([f]).verify(0, d)["status"], "unknown")
        following["heroes"]["128"]["buttons"]["enigma_black_hole"]["channeling"] = False
        self.assertEqual(reviewer([f, following]).verify(0, d)["status"], "unknown")
        f["heroes"]["128"]["buttons"]["enigma_black_hole"]["channeling"] = False
        self.assertEqual(reviewer([f]).verify(0, d)["status"], "blocked")
        del f["heroes"]["128"]["buttons"]["enigma_black_hole"]["channeling"]
        self.assertEqual(reviewer([f]).verify(0, d)["status"], "unknown")

    def test_lazy_frames_match_eager_and_reject_invalid_match_and_missing_time(self):
        f = sample()
        loaded = []
        def window(lo, hi):
            loaded.append((lo, hi))
            return [f]
        timeline = {"format": "pro-replay-truth-v1", "match_id": 123, "frame_window": window}
        v = ReplayVerifier(timeline, match_id=123, patch_version="7.41f", game_mode=22,
                           hero_ids=[14, 26, 27, 72, 70, 25, 22, 24, 23, 20])
        self.assertEqual(v.verify(0, selection()), reviewer([f]).verify(0, selection()))
        self.assertEqual(loaded, [(95, 99)])
        self.assertEqual(ReplayVerifier(timeline, match_id=124).verify(0, selection())["status"], "unknown")
        self.assertEqual(v.verify(0, selection(time=None))["status"], "unknown")
        self.assertEqual(v.verify(0, selection(target_slot=0, focus_slot=1))["status"], "unknown")
