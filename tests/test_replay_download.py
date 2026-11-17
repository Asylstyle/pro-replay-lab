import bz2
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from pro_replay import replay_download as download


RAW = {"match_id": 9876, "cluster": 442, "replay_salt": 123}
URL = "http://replay442.valve.net/570/9876_123.dem.bz2"
# Small Source 2 envelope with a complete uncompressed DEM_FileInfo packet.
DEMO = download.MAGIC + (16).to_bytes(4, "little") + bytes(4) + bytes([2, 60, 4]) + b"info"


def response(payload, length=None):
    body = BytesIO(payload)
    body.headers = {"Content-Length": str(len(payload) if length is None else length)}
    return body


class ReplayDownloadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache = Path(tmp.name)
        opener = patch.object(download, "build_opener")
        self.open = opener.start().return_value.open
        self.addCleanup(opener.stop)
        sleeper = patch.object(download.time, "sleep")
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_url_uses_match_identity_and_coordinator_fields(self):
        self.assertEqual(download.replay_url(RAW), URL)
        self.assertEqual(download.replay_url({"match_id": 9876, "replay_url": URL}), URL)
        self.assertEqual(download.replay_url({**RAW, "replay_url": URL.replace("9876", "5555")}), URL)
        self.assertIn("replay415.dota2.com.cn", download.replay_url({**RAW, "cluster": 415}))
        for url in (URL.replace("9876", "9877"), URL.replace("valve.net", "valve.net.other"),
                    URL.replace("http:", "file:"), URL + "?else", URL.replace("123.dem", "10000000000.dem")):
            with self.subTest(url=url), self.assertRaises(download.ReplayDownloadError):
                download.replay_url({"match_id": 9876, "replay_url": url})
        for match_id in (True, 0, -1, "9876"):
            with self.assertRaises(download.ReplayDownloadError):
                download.ensure_replay({**RAW, "match_id": match_id}, self.cache)
        self.open.assert_not_called()

    def test_reads_bzip2_and_reuses_complete_cache_without_url(self):
        self.open.return_value = response(bz2.compress(DEMO))
        dest = download.ensure_replay(RAW, self.cache)
        self.assertEqual(dest.read_bytes(), DEMO)
        self.assertEqual(download.ensure_replay({"match_id": 9876}, self.cache), dest)
        self.assertEqual(self.open.call_count, 1)
        self.assertEqual(list(self.cache.iterdir()), [dest])

    def test_replaces_truncated_cache_only_after_successful_download(self):
        existing = self.cache / "9876.dem"
        existing.write_bytes(DEMO[:-2])
        self.open.return_value = response(b"error page")
        with self.assertRaises(download.ReplayDownloadError):
            download.ensure_replay(RAW, self.cache)
        self.assertEqual(existing.read_bytes(), DEMO[:-2])
        self.open.return_value = response(DEMO)
        self.assertEqual(download.ensure_replay(RAW, self.cache).read_bytes(), DEMO)

    def test_retries_incomplete_network_response_but_not_expired_demo(self):
        self.open.side_effect = [response(DEMO[:8], len(DEMO)), response(DEMO)]
        self.assertEqual(download.ensure_replay(RAW, self.cache).read_bytes(), DEMO)
        self.assertEqual(self.open.call_count, 2)
        self.open.reset_mock()
        self.open.side_effect = HTTPError(URL, 404, "gone", {}, BytesIO())
        with self.assertRaisesRegex(download.ReplayDownloadError, "недоступен"):
            download.ensure_replay({**RAW, "match_id": 111}, self.cache)
        self.assertEqual(self.open.call_count, 1)
        self.assertFalse((self.cache / "111.dem").exists())

    def test_compressed_and_expanded_size_limits_leave_no_partial_files(self):
        for limit in ("MAX_DOWNLOAD", "MAX_DEMO"):
            self.open.return_value = response(bz2.compress(DEMO))
            with self.subTest(limit=limit), patch.object(download, limit, 12), self.assertRaises(download.ReplayDownloadError):
                download.ensure_replay(RAW, self.cache)
            self.assertEqual(list(self.cache.iterdir()), [])

    def test_rejects_truncated_or_incorrect_data(self):
        for bad in (DEMO[:13], DEMO[:-1], DEMO[:16] + bytes([8, 1, 1, 0]), bz2.compress(DEMO)[:-2],
                    b"plain text reply", download.ZSTD_MAGIC + bytes(15)):
            self.open.return_value = response(bad)
            with self.subTest(length=len(bad)), self.assertRaises(download.ReplayDownloadError):
                download.ensure_replay(RAW, self.cache)
            self.assertEqual(list(self.cache.iterdir()), [])

    def test_decodes_zstandard_content_advertised_as_bzip2(self):
        try:
            from compression import zstd
        except ImportError:
            try:
                from backports import zstd
            except ImportError:
                self.skipTest("optional Zstandard dependency on earlier Python")
        self.open.return_value = response(zstd.compress(DEMO))
        self.assertEqual(download.ensure_replay(RAW, self.cache).read_bytes(), DEMO)
