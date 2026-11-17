import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pro_replay.archive import Archive, normalize
from pro_replay.cli import main
from pro_replay.impact import contribution, prior_maps, observations
from pro_replay.match_report import render_match
from test_pipeline import fake_api


def scorecard():
    players = []
    for i in range(5):
        players.append({"player_slot": 128 + i, "account_id": 100 + i, "hero_id": 10 + i,
                        "name": f"P{i}", "kills": i, "assists": 4 - i, "deaths": i,
                        "hero_damage": 100 * i, "tower_damage": 40 * i, "stuns": 10 if i == 0 else 0,
                        "hero_healing": 800 if i == 0 else 0, "obs_placed": 3 if i == 0 else 0,
                        "observer_kills": 0, "purchase_log": [], "kills_log": [], "obs_log": []})
    players[-1].update(purchase_log=[{"time": 590, "key": "blink"}],
                       kills_log=[{"time": 600, "key": "npc_dota_hero_lina"}],
                       times=[0, 600, 1200], lh_t=[0, 50, 105], gold_t=[0, 4300, 7300], xp_t=[0, 3900, 8900])
    fight = {"start": 590, "end": 640, "players": [{} for _ in range(10)]}
    fight["players"][9] = {"damage": 3200, "killed": {"npc_dota_hero_lina": 1}, "gold_delta": -100}
    return {"match_id": 999, "start_time": 2000, "patch": 60, "leagueid": 1, "duration": 1500,
            "dire_score": 10, "radiant_win": False, "players": list(reversed(players)), "teamfights": [fight]}


class MatchAnalyticsTests(unittest.TestCase):
    def test_common_denominators_and_support_credit(self):
        players = scorecard()["players"]
        scores = [contribution(players, s, {}) for s in range(128, 133)]
        self.assertAlmostEqual(sum(d["score"] for d in scores), 1)
        self.assertAlmostEqual(scores[0]["score"], .35)
        self.assertEqual(scores[0]["available"], 6)
        self.assertEqual(scores[0]["metrics"][4]["share"], 1)
        players[-1].pop("stuns")
        for p in players:
            p["hero_healing"] = 0
        out = contribution(players, 128, {})
        self.assertEqual(out["available"], 4)
        self.assertIsNone(out["metrics"][3]["total"])
        self.assertEqual(out["metrics"][5]["total"], 0)
        self.assertAlmostEqual(sum(p["score"] for p in out["roster"]), 1)

    def test_partial_roster_and_tied_lead(self):
        players = scorecard()["players"]
        self.assertIsNone(contribution(players[:-1], 128, {}))
        self.assertIsNone(contribution(players[:4] + [players[0]], 128, {}))
        self.assertFalse(contribution(players, 128, {})["metrics"][0]["sole_leader"])
        sparse = [{"player_slot": n, "hero_damage": n, "tower_damage": 0} for n in range(5)]
        self.assertIsNone(contribution(sparse, 0, {})["score"])

    def test_team_comparison_uses_known_logs_and_exact_slot(self):
        raw = scorecard()
        raw["players"][0]["name"] = "<img src=x>"
        row = normalize(raw, 104, {})
        self.assertEqual(row["detail"]["teamfights"][0]["damage"], 3200)
        self.assertEqual([p["lh"] for p in row["detail"]["checkpoints"]], [50, 105])
        html = render_match(row, [row])
        self.assertIn("Единственный из пяти", html)
        self.assertIn("&lt;img src=x&gt;", html)
        self.assertNotIn("<img src=x>", html)
        del raw["players"][2]["kills_log"]
        self.assertNotIn("Единственный из пяти", render_match(normalize(raw, 104, {}), []))

    def test_comparisons_exclude_future_duplicates_and_other_versions(self):
        selected = dict(normalize(scorecard(), 104, {}), patch_version="7.41f")
        prior = dict(selected, start_time=1900, match_id=998)
        later = dict(prior, match_id=1000, start_time=2010)
        other = dict(prior, match_id=997, patch_version="7.41e")
        history = prior_maps(selected, [selected, prior, prior, later, other, dict(prior, account_id=9)])
        self.assertEqual(history, [prior])
        patterns, _ = observations(selected, history)
        self.assertEqual((patterns[0]["previous"], patterns[0]["comparable"]), (1, 1))

    def test_match_command_upgrades_old_cache_then_renders_offline(self):
        def respond(path, params):
            return [] if path == "/heroes" else scorecard()
        with tempfile.TemporaryDirectory() as folder, redirect_stdout(io.StringIO()):
            db = str(Path(folder) / "cache.sqlite")
            page = Path(folder) / "my match.html"
            a = Archive(db)
            a.save({"account_id": 104, "match_id": 999, "start_time": 2000})
            a.save_patches([(1500, "7.41e"), (2000, "7.41f"), (2010, "7.42")])
            a.db.close()
            args = ["--db", db, "match", "104", "999", "--out", str(page)]
            with fake_api(respond) as api, patch("pro_replay.cli.OpenDota", return_value=api):
                self.assertEqual(main(args), 0)
            with patch("pro_replay.source.OpenDota._get", side_effect=AssertionError("unexpected network call")):
                self.assertEqual(main(args), 0)
            self.assertIn("7.41f", page.read_text(encoding="utf8"))
            index = Path(folder) / "all maps.html"
            self.assertEqual(main(["--db", db, "report", "104", "--out", str(index), "--details"]), 0)
            self.assertIn('href="all%20maps-matches/999.html"', index.read_text(encoding="utf8"))
            self.assertTrue((index.parent / "all maps-matches" / "999.html").is_file())
