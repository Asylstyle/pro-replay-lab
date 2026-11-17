from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pro_replay.replay_files import EvidenceError, _index_frames, replay_evidence


class FrameCacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.frames = self.root / "out.json"
        self.db = self.root / "index.sqlite"
        self.text = ('{"format":"pro-replay-truth-v1","match_id":123,"frames":[\n'
                     '{"time":5,"tick":300,"heroes":{"0":{"mana":70}}},\n'
                     '{"time":6,"tick":330,"heroes":{"0":{"mana":72}}}\n'
                     '],"events":[\n{"time":6,"kind":"death","actor_slot":128,"target_slot":0}\n]}\n')
        self.frames.write_text(self.text, encoding="utf8")

    def test_on_disk_windows_and_events_are_indexed_by_game_time(self):
        _index_frames(self.frames, self.db, 123)
        with patch("pro_replay.replay_files._prepare", return_value=self.db):
            with replay_evidence({"match_id": 123}, self.root) as (timeline, reason):
                self.assertEqual(reason, "")
                frame, = timeline["frame_window"](5, 5)
                self.assertEqual(frame["heroes"]["0"]["mana"], 70)
                self.assertEqual(timeline["frame_window"](7, 9), [])
                self.assertEqual(timeline["events"][0]["target_slot"], 0)

    def test_a_short_replay_without_combat_events_can_be_indexed(self):
        self.frames.write_text(self.text.replace(
            '{"time":6,"kind":"death","actor_slot":128,"target_slot":0}', ""), encoding="utf8")
        _index_frames(self.frames, self.db, 123)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(json.loads(db.execute("SELECT events FROM info").fetchone()[0]), [])

    def test_different_match_and_partial_output_cannot_publish_an_index(self):
        with self.assertRaises(EvidenceError):
            _index_frames(self.frames, self.db, 124)
        self.frames.write_text(self.text.rsplit("]}", 1)[0], encoding="utf8")
        with self.assertRaises(EvidenceError):
            _index_frames(self.frames, self.db, 123)
        with closing(sqlite3.connect(self.db)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM info").fetchone()[0], 0)

    def test_download_and_decode_are_reused_on_repeat_selection(self):
        (self.root / "runtime.json").write_text(json.dumps({"reader_sha256": "1"*64, "libraries": []}))
        with (patch("pro_replay.replay_files.DECODER", self.root),
              patch("pro_replay.replay_files.ensure_replay", return_value=self.root/"source.dem") as fetch,
              patch("pro_replay.replay_files._decode", side_effect=lambda demo,out,cache,bundle:
                    out.write_text(self.text, encoding="utf8")) as decode):
            for _ in range(2):
                with replay_evidence({"match_id": 123}, self.root) as (timeline, _reason):
                    self.assertEqual(timeline["match_id"], 123)
            self.assertEqual((fetch.call_count, decode.call_count), (1, 1))
