import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from pro_replay.source import SourceError
from pro_replay.youtube_captions import download_captions, video_id


VIDEO = "Xqapp58b9_Q"
TOKEN = "example-bearer"
VTT = "WEBVTT\r\n\r\n00:00:01.000 --> 00:00:04.000\r\nOne wave.\r\n"


def track(track_id, language="en", **extra):
    return {"id": track_id, "snippet": {"language": language, "status": "serving", **extra}}


class CaptionTests(unittest.TestCase):
    def test_video_identifiers_and_origin_validation(self):
        for value in (VIDEO, f"https://youtu.be/{VIDEO}?t=2", f"https://m.youtube.com/watch?v={VIDEO}",
                      f"https://youtube.com/embed/{VIDEO}", f"https://www.youtube.com/shorts/{VIDEO}",
                      f"https://www.youtube.com/live/{VIDEO}"):
            with self.subTest(value=value):
                self.assertEqual(video_id(value), VIDEO)

        bad = (f"https://youtube.com.example/watch?v={VIDEO}", f"https://user@youtube.com/watch?v={VIDEO}",
               f"https://youtu.be/{VIDEO}/extra", f"http://youtu.be/{VIDEO}", f"https://youtu.be:8000/{VIDEO}",
               f"https://www.youtube.com/watch?v={VIDEO}&v={VIDEO}", "https://youtu.be/abc", "https://[")
        with patch("pro_replay.youtube_captions.build_opener") as builder:
            for value in bad:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    download_captions(value, "en", TOKEN)
            for language, token in (("", TOKEN), ("x\r\ny", TOKEN), ("en", ""), ("en", TOKEN + "\r\nHeader: x")):
                with self.assertRaises(ValueError):
                    download_captions(VIDEO, language, token)
            builder.assert_not_called()

    def test_download_exact_language_and_prefer_manual_served_track(self):
        choices = [track("other", "en-US"), track("generated", trackKind="ASR"),
                   track("draft", isDraft=True), track("partial", trackKind="forced"),
                   track("failed", status="failed"), track("editor/track")]
        with patch("pro_replay.youtube_captions.build_opener") as builder:
            opener = builder.return_value
            opener.open.side_effect = [io.BytesIO(json.dumps({"items": choices}).encode()),
                                       io.BytesIO(VTT.encode("utf-8-sig"))]
            self.assertEqual(download_captions(VIDEO, "EN", TOKEN), VTT)
            requests = [c.args[0] for c in opener.open.call_args_list]
            self.assertIn("videoId=" + VIDEO, requests[0].full_url)
            self.assertTrue(requests[1].full_url.endswith("/editor%2Ftrack?tfmt=vtt"))
            for call in opener.open.call_args_list:
                request = call.args[0]
                self.assertEqual(urlsplit(request.full_url).netloc, "www.googleapis.com")
                self.assertEqual(urlsplit(request.full_url).scheme, "https")
                self.assertNotIn(TOKEN, request.full_url)
                self.assertEqual(request.unredirected_hdrs["Authorization"], "Bearer " + TOKEN)
                self.assertNotIn("Authorization", request.headers)
                self.assertLessEqual(call.kwargs["timeout"], 30)
            handler = builder.call_args.args[0]
            self.assertIsNone(handler.redirect_request(requests[1], None, 302, "", {}, "https://elsewhere.invalid"))

    def test_asr_fallback_and_absent_language(self):
        tracks = json.dumps({"items": [track("captions", "pt-BR", trackKind="ASR")]}).encode()
        with patch("pro_replay.youtube_captions.build_opener") as builder:
            opener = builder.return_value
            opener.open.side_effect = [io.BytesIO(tracks), io.BytesIO(VTT.encode()), io.BytesIO(tracks)]
            self.assertEqual(download_captions(VIDEO, "pt-br", TOKEN), VTT)
            with self.assertRaisesRegex(SourceError, "pt-PT"):
                download_captions(VIDEO, "pt-PT", TOKEN)
            self.assertEqual(opener.open.call_count, 3)

    def test_service_errors_hide_credentials_and_remote_response(self):
        with patch("pro_replay.youtube_captions.build_opener") as builder:
            opener = builder.return_value
            opener.open.side_effect = [
                HTTPError("https://server.invalid/" + TOKEN, 403, TOKEN, {}, io.BytesIO(TOKEN.encode())),
                URLError(TOKEN),
            ]
            for status in (403, None):
                with self.assertRaises(SourceError) as error:
                    download_captions(VIDEO, "en", TOKEN)
                self.assertEqual(error.exception.status, status)
                self.assertNotIn(TOKEN, str(error.exception))
                self.assertTrue(error.exception.__suppress_context__)

    def test_bad_service_bodies_are_rejected(self):
        tracks = json.dumps({"items": [track("cap")]}).encode()
        with patch("pro_replay.youtube_captions.build_opener") as builder:
            opener = builder.return_value
            opener.open.side_effect = [io.BytesIO(b"{}"), io.BytesIO(b"\xff"), io.BytesIO(tracks),
                                       io.BytesIO(b"<html>unexpected</html>")]
            for _ in range(3):
                with self.assertRaises(SourceError):
                    download_captions(VIDEO, "en", TOKEN)
        with patch("pro_replay.youtube_captions._LIMIT", 4), patch("pro_replay.youtube_captions.build_opener") as builder:
            builder.return_value.open.return_value = io.BytesIO(b"12345")
            with self.assertRaisesRegex(SourceError, "8 МиБ"):
                download_captions(VIDEO, "en", TOKEN)
