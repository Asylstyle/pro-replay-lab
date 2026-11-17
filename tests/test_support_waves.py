from copy import deepcopy
import unittest

from pro_replay.support_waves import render_support_waves, support_wave_review
from pro_replay.wave_tracks import trajectory_windows


class SupportWavesTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"match_id": 18, "duration": 420, "players": [
            {"player_slot": 128, "hero_id": 11, "lane": 1, "position_est": 4},
            {"player_slot": 129, "hero_id": 12, "lane": 1, "position_est": 3,
             "times": [60, 120, 300], "lh_t": [6, 11, 34]},
            {"player_slot": 2, "hero_id": 22, "lane": 1, "position_est": 1},
        ]}
        self.frame = {"time": 75, "tick": 123, "heroes": {
            "128": {"hero_id": 11, "alive": True, "x": 0, "y": 0},
            "129": {"hero_id": 12, "alive": True, "x": 0, "y": 500}}}
        self.timeline = {"match_id": 18, "format": "pro-replay-truth-v1", "frames": [self.frame]}

    def test_roles_side_duration_and_incomplete_data(self):
        self.assertIsNone(support_wave_review(self.raw, 128, 1))
        self.assertIsNone(support_wave_review(self.raw, 132, 5))
        self.assertIsNone(support_wave_review(dict(self.raw, duration=40), 128, None))
        self.assertEqual(render_support_waves(None), "")
        value = support_wave_review(dict(self.raw, duration=100), 128, None)
        self.assertEqual(value["side"], "Dire")
        self.assertEqual(value["position"], 4)
        self.assertEqual([p["time"] for p in value["checkpoints"]], [75])
        self.assertIn("1:00 — 6", value["checkpoints"][0]["evidence"][0])
        self.assertNotIn("2:00 — 11", str(value))

    def test_indexed_replay_and_death_override(self):
        timeline = dict(self.timeline, frame_window=lambda lo, hi: [f for f in [self.frame] if lo <= f["time"] <= hi])
        result = support_wave_review(self.raw, 128, 5, timeline)
        point = result["checkpoints"][0]
        self.assertEqual(point["video_time"], 325)
        self.assertIn("500 ед.", " ".join(point["evidence"]))
        self.frame["heroes"]["128"]["alive"] = False
        point = support_wave_review(self.raw, 128, None, timeline)["checkpoints"][0]
        self.assertIn("мёртв", point["evidence"][0])
        self.assertIn("до смерти", point["review"])
        self.assertNotIn("500 ед.", str(point))

    def test_no_assumption_from_wrong_replay_hero_missing_lane_or_ambiguous_partner(self):
        timeline = dict(self.timeline, match_id=15)
        result = support_wave_review(self.raw, 128, None, timeline)
        self.assertNotIn("500 ед.", str(result))
        self.frame["heroes"]["128"]["hero_id"] = 111
        self.assertNotIn("В кадре повтора", str(support_wave_review(self.raw, 128, None, self.timeline)))
        self.raw["players"].append(dict(self.raw["players"][1], player_slot=130))
        self.assertFalse(support_wave_review(self.raw, 128, None)["checkpoints"][0]["evidence"])
        self.raw["players"][0].pop("lane")
        result = support_wave_review(self.raw, 128, None)
        html = render_support_waves(result)
        self.assertIn("ручной проверки", html)
        self.assertIn("перетяжки вражеской волны", html)
        self.assertIn("При стаке", html)
        self.assertIn("t=325s", html)
        self.assertIn("5:25 в видео", html)
        self.assertNotIn("подтверждённый отвод", html.lower())

    def test_friendly_creeps_follow_same_neutral_back_towards_its_start(self):
        frames = []
        for t, neutral_x, creep_x in [(76, 0, 950), (80, 440, 620), (84, 80, 165)]:
            units = [{"kind": "neutral", "id": 994, "team": 4, "x": neutral_x, "y": 0},
                     *[{"kind": "lane", "id": uid, "team": 3, "x": creep_x, "y": dy}
                       for uid, dy in [(31, 30), (77, 65)]]]
            frames.append({"time": t, "tick": 50*t, "wave_units": units, "heroes": {
                "128": {"hero_id": 11, "alive": True, "x": neutral_x + 90, "y": 35}}})
        self.timeline["frames"] = frames
        result = support_wave_review(self.raw, 128, 4, self.timeline)
        self.assertEqual(result["trajectories"], [{"time": 76, "end": 84, "kind": "camp", "creeps": 2}])
        self.assertIn("1:16–1:24", render_support_waves(result))
        frames[-1]["wave_units"][0]["id"] = 995
        self.assertFalse(trajectory_windows(self.timeline, 128, 11, 420))

    def test_enemy_wave_leaves_live_tower_axis_with_actor_but_standard_lane_motion_does_not(self):
        frames = []
        for t, side in [(316, 50), (320, 395), (324, 800)]:
            units = [{"kind": "tower", "id": tier, "team": 2, "x": x, "y": 0, "name": f"dota_goodguys_tower{tier}_top"}
                     for tier, x in [(1, 0), (2, 1300)]]
            units += [{"kind": "lane", "id": uid, "team": 2, "x": x, "y": side - 30}
                      for uid, x in [(113, 530), (188, 580)]]
            frames.append({"time": t, "tick": t*30, "wave_units": units, "heroes": {
                "128": {"hero_id": 11, "alive": True, "x": 645, "y": side}}})
        self.timeline["frames"] = frames
        self.assertEqual(trajectory_windows(self.timeline, 128, 11, 420),
                         [{"time": 316, "end": 324, "kind": "drag", "creeps": 2}])
        normal = deepcopy(frames)
        for f in normal:
            for unit in f["wave_units"][2:] + [f["heroes"]["128"]]:
                unit.update(x=1200 - unit["y"], y=30)
        self.assertFalse(trajectory_windows(dict(self.timeline, frames=normal), 128, 11, 420))
        self.assertFalse(trajectory_windows(self.timeline, 128, 11, 319))

    def test_snapfire_camp_sequence_from_real_demo_9093297478(self):
        # Three real decoded samples. Hero positions are rounded here to a world unit.
        samples = [
            (472, (-5483, 3887), (-5079, 3987), (-6444, 2704), (-6453, 2609)),
            (476, (-6127, 3818), (-5739, 3700), (-5978, 3618), (-6053, 3618)),
            (480, (-6178, 4152), (-5588, 3798), (-5772, 3606), (-5893, 3687)),
        ]
        frames = []
        for t, hero, neutral, first, second in samples:
            units = [{"id": uid, "kind": kind, "team": team, "x": xy[0], "y": xy[1]}
                     for uid, kind, team, xy in [(13305511, "neutral", 4, neutral),
                                                 (15681559, "lane", 2, first), (10504691, "lane", 2, second)]]
            frames.append({"time": t, "tick": t * 30, "wave_units": units, "heroes": {
                "4": {"hero_id": 128, "alive": True, "x": hero[0], "y": hero[1]}}})
        self.assertEqual(trajectory_windows({"frames": frames}, 4, 128, 480),
                         [{"time": 472, "end": 480, "kind": "camp", "creeps": 2}])
