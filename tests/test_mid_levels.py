import unittest

from pro_replay.mid_levels import level_review, render_level_review


class MidLevelTests(unittest.TestCase):
    def setUp(self):
        self.pair = {"status": "available", "patch": "7.41f",
                     "self": {"id": 86, "slot": 0, "name": "Rubick"},
                     "opponent": {"id": 16, "slot": 128, "name": "Sand King"}}
        self.raw = {"match_id": 99, "duration": 600, "players": [
            {"player_slot": 0, "times": [0, 60, 120], "xp_t": [0, 300, 650]},
            {"player_slot": 128, "times": [0, 60, 120], "xp_t": [0, 180, 520]}]}

    def frame(self, t, own, rival, rank, *, visible=True, alive=True):
        return {"time": t, "tick": 1000+30*t, "heroes": {
            "0": {"hero_id": 86, "alive": True, "level": own, "buttons": {
                "rubick_fade_bolt": {"level": rank, "kind": "ability"}}},
            "128": {"hero_id": 16, "alive": alive, "level": rival, "visible_to": {"2": visible}}}}

    def test_runs_require_consecutive_samples_and_both_alive(self):
        frames = [self.frame(47,1,1,0), self.frame(48,2,1,0,visible=False),
                  self.frame(49,2,1,1), self.frame(52,2,1,1), self.frame(53,2,1,1,alive=False)]
        timeline = {"format": "pro-replay-truth-v1", "match_id": 99, "frames": frames}
        result = level_review(self.raw, self.pair, timeline)
        self.assertEqual([(r["start"], r["end"], r["seen_seconds"]) for r in result["runs"]],
                         [(48,49,1),(52,52,1)])
        self.assertEqual([(u["time"],u["rank"]) for u in result["upgrades"]], [(49,1)])
        self.assertIn("0:48–0:49", render_level_review(result, self.pair))

    def test_wrong_replay_uses_only_simultaneous_journal_measurements(self):
        invalid = {"format":"pro-replay-truth-v1", "match_id": 98, "frames":[self.frame(2,6,1,1)]}
        result = level_review(self.raw, self.pair, invalid)
        self.assertEqual(result["samples"], 0)
        self.assertEqual(result["journal"], [(0,1,1),(60,2,1),(120,3,2)])
        self.assertIn("Снимки опыта OpenDota", render_level_review(result, self.pair))
        self.raw["players"][1]["xp_t"] = [0, 900, 400]
        self.assertEqual(level_review(self.raw, self.pair)["journal"], [])
