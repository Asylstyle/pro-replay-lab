import copy
from contextlib import closing
import sqlite3
import unittest

from scripts.collect_patch_ranked import capture, initialize


def live(rating):
    return {"match_id": "100", "average_mmr": rating, "last_update_time": 1700000000,
            "league_id": 0, "lobby_type": 7, "game_mode": 22,
            "players": [{"team": side, "team_slot": slot, "hero_id": slot + side * 5,
                         "account_id": 999999} for side in (0, 1) for slot in range(1, 6)]}


class LiveCaptureTests(unittest.TestCase):
    def test_first_complete_observation_is_kept_across_captures(self):
        with closing(sqlite3.connect(":memory:")) as db:
            initialize(db)
            row = live(7300)
            incomplete = copy.deepcopy(row)
            incomplete["players"][0]["hero_id"] = 0
            stats = capture(db, [incomplete, row], "2026-11-10T12:00:00+00:00")
            self.assertEqual(stats, {"missing_mmr_or_roster": 1, "qualifying_observations": 1})
            capture(db, [row], "2026-11-11T12:00:00+00:00")
            result = db.execute("SELECT status,evidence FROM candidates").fetchall()
            self.assertEqual(len(result), 1)
            status, evidence = result[0]
            self.assertEqual(status, "awaiting_postgame")
            self.assertIn("2026-11-10", evidence)
            self.assertNotIn("account_id", evidence)

    def test_contradictory_rating_never_sneaks_across_threshold(self):
        for sequence in ((7299, 7300), (7300, 7299), (7300, 7400, 7300)):
            with self.subTest(sequence=sequence), closing(sqlite3.connect(":memory:")) as db:
                initialize(db)
                for rating in sequence:
                    capture(db, [live(rating)], "2026-11-10T12:00:00+00:00")
                self.assertEqual(db.execute("SELECT status,card FROM candidates").fetchone(),
                                 ("conflicting_mmr", None))
