import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import closing, nullcontext, redirect_stdout
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pro_replay.archive import Archive
from pro_replay.cli import main
from pro_replay.coach_model import load_coach_model
from pro_replay.coach_report import render_session
from pro_replay.coaching import make_session
from pro_replay.patch_reference import load_reference, reference_for_player, train_reference
from pro_replay.reference_policy import compact_live_evidence, make_policy
from pro_replay.source import SourceError
from tests.test_coach_model import replay


class Tables(HTMLParser):
    """Only the visible cell values are relevant to these comparisons."""
    def __init__(self):
        super().__init__()
        self.rows = []
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("th", "td"):
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self.cell is not None:
            self.rows[-1].append("".join(self.cell))
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data.replace("\u202f", " ").replace("\xa0", " "))


class PatchBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.games = [dict(replay(i), human_players=10) for i in range(40)]
        cls.start = cls.games[0]["start_time"]
        cls.as_of = cls.games[-1]["start_time"] + 1900
        cls.policy = make_policy([(cls.start, "7.41f")], as_of=cls.as_of)
        cls.games[31]["duration"] = 9000  # Ends during the first test game.
        for i, raw in enumerate(cls.games):
            for j, p in enumerate(raw["players"]):
                p["position_est"] = j % 5 + 1  # Observed role is deliberately different from farm rank.
                p["account_id"] = 1000 + j
            if i % 4 != 0:
                continue
            raw.update(leagueid=0, lobby_type=7, game_mode=22)
            spectator = {"match_id": str(raw["match_id"]), "average_mmr": 7300, "league_id": 0,
                         "lobby_type": 7, "game_mode": 22, "last_update_time": raw["start_time"]+120,
                         "players": [{"team": j // 5, "team_slot": j % 5 + 1, "hero_id": p["hero_id"]}
                                     for j, p in enumerate(raw["players"])]}
            observed = datetime.fromtimestamp(raw["start_time"]+200, timezone.utc).isoformat()
            raw["opendota_live"] = compact_live_evidence(spectator, observed_at_utc=observed)
        # Both recipes run in stdlib. Exercise real folds, selection, holdout and refit without optional ML packages.
        with patch("pro_replay.patch_reference.RECIPES", ("rank_median", "ridge_legacy")), \
                patch.dict(sys.modules, {"threadpoolctl": SimpleNamespace(threadpool_limits=lambda **kw: nullcontext())}):
            cls.model = train_reference(cls.games, cls.policy)

    def test_arithmetic_means_separate_populations_and_position_from_lane_farm(self):
        model = self.model
        self.assertEqual(model["counts"], {"professional": 30, "ranked_7300": 10})
        # Two players per position per game. Ranked games have indices 0,4,...,36 (mean 18).
        ranked = model["position_means"]["ranked_7300"]["1"]
        self.assertEqual(ranked["players"], 20)
        self.assertEqual(ranked["at10"]["lh"], 38)
        self.assertEqual(ranked["at20"]["lh"], 116)
        # Top gold rank is four places above these players (16 extra LH at ten minutes).
        self.assertEqual(model["means"]["ranked_7300"]["1"]["at10"]["lh"], 54)
        professional = model["position_means"]["professional"]["1"]
        self.assertEqual(professional["players"], 60)
        self.assertEqual(professional["at10"]["lh"], 40)
        self.assertEqual(professional["at20"]["lh"], 122)

    def test_player_report_never_uses_20_minute_outcomes_in_forecast(self):
        original = self.games[1]
        changed = copy.deepcopy(original)
        for p in changed["players"]:
            p["position_est"] = 4
            for key in ("lh_t", "gold_t", "xp_t"):
                p[key][2:] = [450000, 500000]
        changed["radiant_win"] = True
        before = reference_for_player(self.model, original, 0, 1)
        after = reference_for_player(self.model, changed, 0, 1)
        self.assertNotEqual([m["actual"] for m in before["metrics"]], [m["actual"] for m in after["metrics"]])
        self.assertEqual([m["expected"] for m in before["metrics"]], [m["expected"] for m in after["metrics"]])
        self.assertEqual(before["groups"], after["groups"])

    def test_new_patch_can_show_means_before_rolling_validation_is_possible(self):
        small = train_reference(self.games[:2], self.policy)
        self.assertIsNone(small["fit"])
        result = reference_for_player(small, self.games[1], 0, 1)
        self.assertEqual(result["groups"]["professional"]["players"], 2)
        self.assertEqual(result["groups"]["ranked_7300"]["players"], 2)
        self.assertTrue(all(m["expected"] is None for m in result["metrics"]))

    def test_refit_has_all_rows_but_temporal_evaluation_excludes_overlapping_games(self):
        validation = self.model["validation"]
        by_id = {g["match_id"]: g for g in self.games}
        earlier = set(validation["selection_train_ids"])
        later = set(validation["holdout_ids"])
        self.assertFalse(earlier & later)
        self.assertEqual(later, {g["match_id"] for g in self.games[32:]})
        self.assertNotIn(self.games[31]["match_id"], earlier)
        for fold in validation["folds"]:
            train = [by_id[k] for k in fold["train_ids"]]
            valid = [by_id[k] for k in fold["validation_ids"]]
            self.assertLessEqual(max(g["start_time"] + g["duration"] for g in train), min(g["start_time"] for g in valid))
            self.assertTrue(set(fold["validation_ids"]) <= earlier)
        self.assertEqual(set(self.model["fit"]["train_match_ids"]), set(by_id))
        winner = validation["winner"]
        self.assertEqual(validation["holdout"]["ranked_7300"][winner]["lh"]["n"], 20)

    def test_serving_verifies_newer_match_patch_and_fails_closed_when_calendar_unavailable(self):
        older = dict(self.games[0], start_time=self.start-1)
        self.assertNotIn("metrics", reference_for_player(self.model, older, 0))
        newer = dict(self.games[0], start_time=self.as_of+100)
        with patch("pro_replay.patch_reference._verified_calendar", return_value=([(self.start, "7.41f")], self.as_of+200)):
            self.assertIn("metrics", reference_for_player(self.model, newer, 0))
        with patch("pro_replay.patch_reference._verified_calendar", return_value=([(self.start, "7.41f"), (self.as_of+50, "7.41g")], self.as_of+200)):
            self.assertNotIn("metrics", reference_for_player(self.model, newer, 0))
        with patch("pro_replay.patch_reference._verified_calendar", side_effect=SourceError("offline")):
            self.assertNotIn("metrics", reference_for_player(self.model, newer, 0))

    def test_client_session_uses_chosen_reference_file_and_renders_both_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "specific.json")
            path.write_text(json.dumps(self.model), encoding="utf8")
            self.assertEqual(load_reference(path)["counts"], self.model["counts"])
            archive = Archive(Path(tmp, "client.sqlite"))
            with closing(archive.db):
                lessons = {"format": "pro-replay-lessons-v1", "segments": [], "idf": {}}
                result = make_session(archive, self.games[1], 0, load_coach_model(), lessons,
                                      "lane", position=1, reference_path=path)
            self.assertTrue(result["patch_reference"]["in_reference"])
            html = render_session(result)
        parsed = Tables()
        parsed.feed(html)
        rows = [r for r in parsed.rows if r and (r[0] == "Про-матчи" or r[0].startswith("Рейтинг 7"))]
        self.assertEqual(len(rows), 2)
        self.assertIn(["20", "38"], [r[1:3] for r in rows])
        self.assertIn(["60", "40"], [r[1:3] for r in rows])
        self.assertIn("Планки экономики на патче", html)

    def test_local_model_precedes_packaged_model_and_cli_accepts_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            data = folder / "data"
            data.mkdir()
            packaged = folder / "patch-reference-model.json"
            packaged.write_text(json.dumps(dict(self.model, test_source="packaged")), encoding="utf8")
            path = data / "patch-reference.json"
            original_cwd = Path.cwd()
            try:
                os.chdir(folder)
                with patch("pro_replay.patch_reference.EXAMPLES", folder):
                    self.assertEqual(load_reference()["test_source"], "packaged")
                    path.write_text(json.dumps(dict(self.model, test_source="local")), encoding="utf8")
                    self.assertEqual(load_reference()["test_source"], "local")
                    self.assertEqual(load_reference(packaged)["test_source"], "packaged")
            finally:
                os.chdir(original_cwd)
            raw = folder / "raw.json"
            out = folder / "report.html"
            raw.write_text(json.dumps(self.games[0]), encoding="utf8")
            with patch("pro_replay.cli.OpenDota.match", side_effect=AssertionError), redirect_stdout(io.StringIO()):
                result = main(["--db", str(data / "cli.db"), "coach", "slot:0", str(self.games[0]["match_id"]),
                               "--focus", "lane", "--journal-only", "--from-json", str(raw),
                               "--reference", str(packaged), "--out", str(out)])
            self.assertEqual(result, 0)
            self.assertIn("30 про-матчей", out.read_text(encoding="utf8"))
