from contextlib import closing
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts import refresh_patch_reference as workflow
from pro_replay.cli import parser, reference_training_inputs
from pro_replay.reference_policy import LIVE_SOURCE, make_policy


def write_corpus(folder, cohort, cards, manifest):
    folder.mkdir(parents=True, exist_ok=True)
    prefix = "patch-reference-" + cohort
    data = gzip.compress(json.dumps({"matches": cards}).encode())
    (folder / (prefix + ".json.gz")).write_bytes(data)
    manifest = dict(manifest, snapshot_sha256=hashlib.sha256(data).hexdigest())
    manifest["matches" if cohort == "ranked" else "match_ids"] = [r["match_id"] for r in cards]
    (folder / (prefix + "-manifest.json")).write_text(json.dumps(manifest), encoding="utf8")


class ReferenceRefreshTests(unittest.TestCase):
    def test_clean_install_trains_on_latest_completed_snapshot_only_for_its_patch(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        receipt = json.loads((examples / workflow.LATEST_RANKED_CAPTURE / "manifest.json").read_text(encoding="utf8"))
        saved = receipt["policy"]
        args = parser().parse_args(["train-reference"])
        with tempfile.TemporaryDirectory() as tmp:
            original = Path.cwd()
            try:
                os.chdir(tmp)
                with patch("pro_replay.cli.time.time", return_value=saved["as_of"] + 100), \
                        patch("pro_replay.cli.valve_patches") as calendar:
                    calendar.return_value = [(saved["patch_start"], saved["patch"])]
                    cards, policy, target = reference_training_inputs(args)
                    pubs = {c["match_id"] for c in cards if c["leagueid"] == 0}
                    self.assertEqual(pubs, set(receipt["matches"]))
                    self.assertEqual(policy["as_of"], saved["as_of"] + 100)
                    self.assertEqual(target, Path("data/patch-reference.json"))
                    calendar.return_value.append((saved["as_of"] + 1, "7.42"))
                    with self.assertRaisesRegex(ValueError, "текущего патча"):
                        reference_training_inputs(args)
            finally:
                os.chdir(original)

    def test_offline_training_uses_frozen_rosters_and_respects_explicit_inputs(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        frozen = examples / "fresh-ranked-2026-11-17" / "manifest.json"
        def inputs(*args):
            return reference_training_inputs(parser().parse_args(["train-reference", *args]))

        with patch("pro_replay.cli.valve_patches", side_effect=AssertionError("offline")):
            cards, policy, target = inputs("--frozen-policy", str(frozen))
            ranked = set(json.loads(frozen.read_text(encoding="utf8"))["matches"])
            professional = set(json.loads((examples / "patch-reference-pro-manifest.json").read_text(encoding="utf8"))["match_ids"])
            self.assertEqual({c["match_id"] for c in cards}, professional | ranked)
            self.assertEqual(target, Path("data/next-reference.json"))
            with tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                selected = folder / "explicit.json"
                selected.write_text(json.dumps(cards[:2]), encoding="utf8")
                chosen, same_policy, out = inputs("--replays", str(selected), "--frozen-policy", str(frozen),
                                                  "--out", str(folder / "test.json"))
                self.assertEqual((chosen, same_policy, out), (cards[:2], policy, folder / "test.json"))
                bare = folder / "policy.json"
                bare.write_text(json.dumps(policy), encoding="utf8")
                chosen, same_policy, out = inputs("--replays", str(selected), "--frozen-policy", str(bare))
                self.assertEqual((chosen, same_policy, out), (cards[:2], policy, Path("data/patch-reference.json")))

                manifest = json.loads(frozen.read_text(encoding="utf8"))
                copied = folder / "manifest.json"
                snapshot = folder / "matches.json.gz"
                snapshot.write_bytes(frozen.with_name("matches.json.gz").read_bytes())
                copied.write_text(json.dumps({**manifest, "policy": {**policy, "patch": "7.42"}}), encoding="utf8")
                with self.assertRaisesRegex(ValueError, "same patch"):
                    inputs("--frozen-policy", str(copied))
                copied.write_text(json.dumps(manifest), encoding="utf8")
                snapshot.write_bytes(b"incomplete copy")
                with self.assertRaisesRegex(ValueError, "checksum"):
                    inputs("--frozen-policy", str(copied))

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
            batch = folder / "examples" / "ranked-collection-2026-11-18"
            batch.mkdir(parents=True)
            snapshot = gzip.compress(b'{"matches":[]}')
            (batch / "matches.json.gz").write_bytes(snapshot)
            evidence = {"match_id": 11, "average_mmr": 7500, "last_update_time": 150}
            ledger = [{"match_id": 11, "status": "awaiting_postgame", "evidence": evidence}]
            manifest = {"matches": [], "included": 0, "observations": ledger, "policy": policy, "source": LIVE_SOURCE,
                        "snapshot_sha256": hashlib.sha256(snapshot).hexdigest()}
            (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf8")
            active = data / "patch-reference.json"
            active.write_bytes(b"existing-model")
            with patch.object(workflow, "REPO", folder), patch.object(workflow.collect_patch_pro, "collect"), \
                    patch.object(workflow.subprocess, "run"), patch.object(workflow, "valve_patches") as calendar, \
                    patch.object(workflow, "train_reference", side_effect=ValueError("insufficient")) as train:
                calendar.return_value = [(100, "7.40")]
                with self.assertRaisesRegex(ValueError, "insufficient"):
                    workflow.refresh(data, 0, 0)
                self.assertEqual(active.read_bytes(), b"existing-model")
                with closing(sqlite3.connect(data / "patch-live.sqlite")) as cache:
                    self.assertEqual(cache.execute("SELECT mid,rating FROM candidates").fetchall(), [(11, 7500)])
                calendar.return_value = [(100, "7.40"), (300, "7.41")]
                with self.assertRaisesRegex(ValueError, "Patch changed"):
                    workflow.refresh(data, 0, 0)
                self.assertEqual(train.call_count, 1)
                self.assertEqual(active.read_bytes(), b"existing-model")

    def test_fresh_ranked_capture_can_complete_pending_cache_without_losing_verified_games(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        fresh = examples / "fresh-ranked-2026-11-17"
        cards, receipt = workflow.read_capture(fresh / "matches.json.gz", fresh / "manifest.json")
        expected = {c["match_id"] for c in cards}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            cache = path / "candidates.sqlite"
            pending = dict(next(r for r in receipt["observations"] if r["status"] == "included"),
                           status="awaiting_postgame")
            write_corpus(path / "older", "ranked", [], {"observations": [pending]})
            workflow._restore_ranked_cache(cache, [path / "older", fresh, examples])
            workflow._restore_ranked_cache(cache, [examples, fresh])
            with closing(sqlite3.connect(cache)) as db:
                ids = {r[0] for r in db.execute("SELECT mid FROM candidates WHERE status='included' AND card IS NOT NULL")}
            self.assertEqual(ids, expected)
