from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import evaluate_fresh_ranked as fresh


class FreshRankedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.examples = fresh.REPO / "examples"
        cls.patch, cls.previous = fresh.published(cls.examples)
        cls.snapshot = cls.examples / "patch-reference-ranked.json.gz"
        cls.manifest = cls.examples / "patch-reference-ranked-manifest.json"
        cls.cards, cls.receipt = fresh.read_capture(cls.snapshot, cls.manifest)
        cls.known = fresh.seen_ids(cls.examples, cls.patch, cls.previous, [])
        cls.last_end = max(cls.patch["fit"]["train_last_end"], cls.previous["ranked_fit"]["train_last_end"])
        cls.policy = dict(cls.receipt["policy"], as_of=fresh.RELEASE_TIME + 100000)

    def card(self, mid, start):
        raw = deepcopy(self.cards[0])
        raw.update(match_id=mid, start_time=start)
        observed = datetime.fromtimestamp(start + 300, timezone.utc).isoformat()
        raw["opendota_live"].update(match_id=mid, last_update_time=start + 280, observed_at_utc=observed)
        return raw

    def test_disjoint_from_known_samples_and_prospective_windows(self):
        before = self.card(1, self.last_end)
        after = self.card(2, self.last_end + 1)
        at_release = self.card(3, fresh.RELEASE_TIME)
        prospective = self.card(4, fresh.RELEASE_TIME + 1)
        result = fresh.evaluate([*self.cards[:2], before, after, at_release, prospective], self.policy,
                                self.patch, self.previous, self.known)
        self.assertEqual(result["excluded"], {"previously_used": 2})
        windows = result["evaluation"]
        self.assertEqual(windows["unseen"]["match_ids"], [1, 2, 3, 4])
        self.assertEqual(windows["after_training"]["match_ids"], [2, 3, 4])
        self.assertEqual(windows["after_release"]["match_ids"], [4])
        self.assertEqual(windows["after_release"]["players"], 10)
        metric = windows["after_release"]["metrics"]
        delta = windows["after_release"]["patch_minus_previous"]["targets"]["xp"]
        self.assertAlmostEqual(delta["mae_delta"], metric["patch"]["xp"]["mae"] - metric["previous"]["xp"]["mae"])
        self.assertIsNone(delta["ci95"])
        self.assertEqual(len(windows["unseen"]["patch_minus_previous"]["targets"]["gold"]["ci95"]), 2)
        audit = result["diagnostics_unseen"]
        self.assertEqual(sum(role["players"] for role in audit["by_estimated_position"].values()), 40)
        worst = audit["largest_patch_absolute_error"]["gold"]
        self.assertGreaterEqual(abs(worst["patch_prediction"] - worst["actual_at_20"]), metric["patch"]["gold"]["mae"])

    def test_unscored_observations_remain_eligible_and_scored_prior_reports_are_excluded(self):
        unscored = [r["match_id"] for r in self.receipt["observations"] if r["status"] == "awaiting_postgame"]
        self.assertTrue(set(unscored) - self.known)
        with tempfile.TemporaryDirectory() as temp:
            prior = Path(temp) / "prior.json"
            mid = next(iter(set(unscored) - self.known))
            prior.write_text(json.dumps({"format": fresh.FORMAT, "evaluation": {"unseen": {"match_ids": [mid]}}}))
            self.assertEqual(fresh.seen_ids(self.examples, self.patch, self.previous, [prior]), self.known | {mid})

    def test_empty_and_malformed_candidates(self):
        bad = self.card(5, fresh.RELEASE_TIME + 1)
        bad["opendota_live"]["average_mmr"] = 7299
        result = fresh.evaluate([bad, self.cards[0]], self.policy, self.patch, self.previous, self.known)
        self.assertEqual(result["excluded"], {"previously_used": 1, "below_7300_average_mmr": 1})
        empty = result["evaluation"]["unseen"]
        self.assertEqual(empty["matches"], 0)
        self.assertIsNone(empty["metrics"]["patch"]["gold"]["mae"])
        self.assertIsNone(empty["patch_minus_previous"]["targets"]["gold"]["ci95"])
        with self.assertRaisesRegex(ValueError, "patch"):
            fresh.evaluate([], dict(self.policy, patch="8.00"), self.patch, self.previous, self.known)

    def test_manifest_source_evidence_integrity_and_unique_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot, manifest = (Path(temp) / name for name in ("cards.gz", "manifest.json"))
            receipt = deepcopy(self.receipt)
            receipt["matches"] = [self.cards[0]["match_id"]]
            receipt["included"] = 1
            evidence = deepcopy(self.cards[0]["opendota_live"])
            receipt["observations"] = [{"match_id": receipt["matches"][0], "status": "included", "evidence": evidence}]
            data = gzip.compress(json.dumps({"matches": self.cards[:1]}).encode())
            snapshot.write_bytes(data)
            receipt["snapshot_sha256"] = hashlib.sha256(data).hexdigest()
            manifest.write_text(json.dumps(receipt))
            self.assertEqual(fresh.read_capture(snapshot, manifest)[0], self.cards[:1])
            evidence["average_mmr"] += 1
            manifest.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "observations"):
                fresh.read_capture(snapshot, manifest)
            data = gzip.compress(json.dumps({"matches": self.cards[:1] * 2}).encode())
            snapshot.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "checksum"):
                fresh.read_capture(snapshot, manifest)
            receipt.update(matches=receipt["matches"] * 2, included=2, snapshot_sha256=hashlib.sha256(data).hexdigest())
            manifest.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, "unique"):
                fresh.read_capture(snapshot, manifest)
