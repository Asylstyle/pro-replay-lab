import gzip
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pro_replay.archive import Archive
from pro_replay.cli import main
from pro_replay.coaching import EXAMPLES, json_file, make_session, save_exercise
from pro_replay.coach_model import load_coach_model
from pro_replay.coach_report import render_session
from test_match_analytics import scorecard


def ranked(mid, *, account=104, role=1, mode=22):
    raw = dict(scorecard(), match_id=mid, start_time=mid * 3000, lobby_type=7, game_mode=mode, leagueid=0)
    raw["players"][0].update(account_id=account, position_est=role)
    return raw


class PracticeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.archive = Archive(self.folder / "client.db")
        self.addCleanup(self.archive.db.close)
        self.model = load_coach_model()
        self.lessons = json_file(EXAMPLES / "coach-lessons-model.json")

    def session(self, raw, focus="lane"):
        return make_session(self.archive, raw, 132, self.model, self.lessons, focus)

    def test_plan_and_progress_only_follow_the_same_account_position_and_mode(self):
        selected = self.session(ranked(3))
        save_exercise(self.archive, selected)
        self.assertIn("Задание сохранено", render_session(selected, home_url="/"))
        self.assertEqual(self.session(ranked(3))["history"]["saved"], "lane")
        for raw in (ranked(1), ranked(6, account=200), ranked(7, role=5), ranked(8, mode=23)):
            self.assertIsNone(self.session(raw)["history"]["previous"])
        later = self.session(ranked(9))
        self.assertEqual(later["history"]["previous"][0], 3)
        self.assertEqual([i for i, _ in later["history"]["prior"]], [3, 1])
        self.assertIn("Проверка прошлого задания", render_session(later))
        self.assertEqual(self.archive.db.execute("select count(*) from coaching_tasks").fetchone()[0], 1)

    def test_hidden_accounts_partial_logs_and_requested_topic(self):
        a = self.session(ranked(11, account=4294967295), "vision")
        self.assertFalse(a["requested_available"])
        self.assertFalse(a["history"]["identified"])
        save_exercise(self.archive, a)
        b = self.session(ranked(12, account=None, role=5))
        self.assertFalse(b["history"]["prior"])
        self.assertIsNone(b["history"]["previous"])
        self.assertIn("опыта", b["cards"][0]["evidence"])
        self.assertIsNone(b["assessment"])
        self.assertIn(b["patch_reference"]["reason"], render_session(b))

    def test_offline_cli_reads_gzip_and_repeats_from_cache(self):
        dest = self.folder / "review.html"
        source = self.folder / "match.json.gz"
        source.write_bytes(gzip.compress(json.dumps(ranked(22)).encode()))
        args = ["--db", str(self.folder / "client.db"), "coach", "104", "22", "--focus", "lane", "--out", str(dest)]
        with redirect_stdout(io.StringIO()), patch("pro_replay.cli.OpenDota.match", side_effect=AssertionError):
            self.assertEqual(main(args + ["--from-json", str(source)]), 0)
            self.assertEqual(main(args), 0)
        self.assertIn("10:00–20:00", dest.read_text(encoding="utf8"))
