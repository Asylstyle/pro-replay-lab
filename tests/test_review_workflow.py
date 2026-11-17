import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from pro_replay.archive import Archive
from pro_replay.cli import main
from test_match_analytics import scorecard
from test_pipeline import fake_api


class ReviewWorkflowTests(unittest.TestCase):
    def test_two_anonymous_players_in_one_ranked_match_stay_separate(self):
        raw = dict(scorecard(), leagueid=0, lobby_type=7, game_mode=22)
        raw["players"][0].pop("account_id")
        raw["players"][1].pop("account_id")
        def respond(path, params):
            return [] if path == "/heroes" else raw

        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            db = str(Path(tmp) / "archive.db")
            page = Path(tmp) / "review.html"
            args = ["--db", db, "match"]
            with fake_api(respond) as api, patch("pro_replay.cli.OpenDota", return_value=api):
                for slot in (131, 132):
                    self.assertEqual(main(args + [f"slot:{slot}", "999", "--out", str(page)]), 0)
            archive = Archive(db)
            try:
                for slot in (131, 132):
                    row = archive.cached_match(999, None, slot)
                    self.assertEqual(row["player_slot"], slot)
                    self.assertIsNone(row["account_id"])
                    self.assertEqual(row["hero_id"], slot - 118)
                    self.assertIn("build", row["detail"])
                    self.assertIn("fight_review", row["detail"])
                    self.assertEqual(len(row["hero_samples"]), 5)
                self.assertEqual(archive.db.execute("SELECT count(*) FROM games").fetchone(), (0,))
                self.assertEqual(archive.db.execute("SELECT count(*) FROM hero_reference").fetchone(), (1,))
            finally:
                archive.db.close()
            with patch("pro_replay.source.OpenDota._get", side_effect=AssertionError("unexpected network access")):
                self.assertEqual(main(args + ["slot:132", "999", "--out", str(page)]), 0)
            html = page.read_text(encoding="utf8")
            for section in ("hero", "fights", "build", "impact"):
                self.assertIn(f'id="{section}"', html)
