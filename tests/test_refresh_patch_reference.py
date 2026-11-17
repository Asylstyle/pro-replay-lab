from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import refresh_patch_reference as workflow
from pro_replay.reference_policy import make_policy


def write_corpus(folder, cohort, cards, manifest):
    folder.mkdir(parents=True, exist_ok=True)
    prefix = "patch-reference-" + cohort
    data = gzip.compress(json.dumps({"matches": cards}).encode())
    (folder / (prefix + ".json.gz")).write_bytes(data)
    manifest = dict(manifest, snapshot_sha256=hashlib.sha256(data).hexdigest())
    manifest["matches" if cohort == "ranked" else "match_ids"] = [r["match_id"] for r in cards]
    (folder / (prefix + "-manifest.json")).write_text(json.dumps(manifest), encoding="utf8")


class ReferenceRefreshTests(unittest.TestCase):
    def test_new_patch_with_no_professional_games_can_still_collect_ranked_games(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            collector = workflow.collect_patch_pro
            with patch.object(collector, "valve_patches", return_value=[(10, "7.42")]), \
                    patch.object(collector, "OpenDota") as api:
                api.return_value._get.return_value = {"rows": []}
                manifest = collector.collect([folder / "cache"], folder, 1, folder / "cache", allow_empty=True)
            self.assertEqual(manifest["matches"], 0)
            self.assertIsNone(manifest["min_start"])
            self.assertEqual(workflow._snapshot(folder, "pro")[0], [])

    def test_disputed_live_record_is_never_revived_by_packaged_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            cache = folder / "candidates.sqlite"
            evidence = {"match_id": 10, "average_mmr": 7400, "last_update_time": 500}
            card = {"match_id": 10, "opendota_live": evidence}
            good = dict(match_id=10, status="included", evidence=evidence)
            bad = dict(good, status="conflicting_mmr")
            write_corpus(folder / "packaged", "ranked", [card], {"observations": [good]})
            write_corpus(folder / "local", "ranked", [], {"observations": [bad]})
            workflow._restore_ranked_cache(cache, [folder / "packaged"])
            with closing(sqlite3.connect(cache)) as db:
                self.assertEqual(db.execute("SELECT status FROM candidates").fetchone()[0], "included")
            workflow._restore_ranked_cache(cache, [folder / "local", folder / "packaged"])
            with closing(sqlite3.connect(cache)) as db:
                self.assertEqual(db.execute("SELECT status,card FROM candidates").fetchall(), [("conflicting_mmr", None)])

    def test_failed_training_and_mid_refresh_patch_change_preserve_active_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            data = folder / "data"
            output = data / "patch-references"
            policy = make_policy([(100, "7.40")], as_of=200)
            write_corpus(output, "ranked", [], {"observations": [], "policy": policy})
            write_corpus(output, "pro", [], {"reference_policy": policy})
            active = data / "patch-reference.json"
            active.write_bytes(b"existing-model")
            with patch.object(workflow, "REPO", folder), patch.object(workflow.collect_patch_pro, "collect"), \
                    patch.object(workflow.subprocess, "run"), patch.object(workflow, "valve_patches") as calendar, \
                    patch.object(workflow, "train_reference", side_effect=ValueError("insufficient")) as train:
                calendar.return_value = [(100, "7.40")]
                with self.assertRaisesRegex(ValueError, "insufficient"):
                    workflow.refresh(data, 0, 0)
                self.assertEqual(active.read_bytes(), b"existing-model")
                calendar.return_value = [(100, "7.40"), (300, "7.41")]
                with self.assertRaisesRegex(ValueError, "Patch changed"):
                    workflow.refresh(data, 0, 0)
                self.assertEqual(train.call_count, 1)
                self.assertEqual(active.read_bytes(), b"existing-model")
