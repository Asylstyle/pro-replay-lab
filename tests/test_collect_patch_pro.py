import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.collect_patch_pro import collect
from tests.test_coach_model import replay


class CurrentProRefreshTests(unittest.TestCase):
    def test_empty_seed_directory_cannot_shrink_same_patch_on_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache, out = root / "seed", root / "result"
            cache.mkdir()
            raw = dict(replay(0), human_players=10)
            (cache / "100.json.gz").write_bytes(gzip.compress(json.dumps(raw).encode()))
            with patch("scripts.collect_patch_pro.valve_patches", return_value=[(1699990000, "7.41f")]):
                first = collect([cache], out, 0, root / "download")
                second = collect([root / "empty"], out, 0, root / "download")
            self.assertEqual(first["snapshot_sha256"], second["snapshot_sha256"])
            self.assertEqual(second["retained_current_patch_ids"], [100])

    def test_patch_change_discards_old_records_from_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache, out = root / "seed", root / "result"
            cache.mkdir()
            (cache / "100.json.gz").write_bytes(gzip.compress(json.dumps(dict(replay(0), human_players=10)).encode()))
            with patch("scripts.collect_patch_pro.valve_patches", return_value=[(1699990000, "7.41e")]):
                collect([cache], out, 0, root / "download")
            (cache / "101.json.gz").write_bytes(gzip.compress(json.dumps(dict(replay(1), human_players=10)).encode()))
            with patch("scripts.collect_patch_pro.valve_patches", return_value=[(1699990000, "7.41e"), (1700001000, "7.41f")]):
                result = collect([cache], out, 0, root / "download")
            self.assertEqual(result["match_ids"], [101])
            self.assertEqual(result["excluded_ids"], {"100": "outside_reference_patch"})
