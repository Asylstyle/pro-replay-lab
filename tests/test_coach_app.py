import json
from http.client import HTTPConnection
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from pro_replay.coach_app import make_server
from pro_replay.source import SourceError
from test_match_analytics import scorecard


class CoachAppTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        lessons = self.folder / "lessons.json"
        lessons.write_text(json.dumps({"format": "pro-replay-lessons-v1", "idf": {}, "segments": []}))
        self.server = make_server(self.folder / "client.sqlite", lessons_path=lessons)
        thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, *, form=None, headers=None):
        conn = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            headers = dict(headers or {})
            if form is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            conn.request(method, path, body=urlencode(form) if form is not None else None, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_client_session_runs_through_http_and_reuses_first_download(self):
        raw = scorecard()
        raw["players"][0]["name"] = "игрок <test>"
        with patch("pro_replay.coach_app.OpenDota.match", return_value=raw) as match:
            status, html = self.request("GET", "/match?match=999")
            self.assertEqual(status, 200)
            roster = html.decode()
            self.assertIn("игрок &lt;test&gt;", roster)
            self.assertIn('value="132"', roster)
            self.assertIn("Состав недоступен", roster)
            status, html = self.request("POST", "/coach", form={"match": 999, "slot": 132, "focus": "fights"})
            self.assertEqual(status, 200)
            lesson = html.decode()
            self.assertIn("Задание на следующую игру", lesson)
            self.assertIn('/detail?match=999&amp;slot=132', lesson)
            self.assertIn('href="/"', lesson)
            status, saved = self.request("POST", "/practice", form={"match": 999, "slot": 132, "focus": "fights"},
                                         headers={"Origin": f"http://127.0.0.1:{self.server.server_port}",
                                                  "Sec-Fetch-Site": "same-origin"})
            self.assertEqual(status, 200)
            self.assertIn("Задание сохранено", saved.decode())
            status, detail = self.request("GET", "/detail?match=999&slot=132")
            self.assertEqual(status, 200)
            self.assertIn("Купил Blink Dagger", detail.decode())
            match.assert_called_once_with(999)

    def test_offline_example_and_home(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        (self.folder / "coach-kez.html").write_bytes(b"<h1>Kez</h1>")
        (self.folder / "coach-kez.png").write_bytes(b"image")
        with patch("pro_replay.coach_app.EXAMPLES", self.folder):
            status, html = self.request("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn(b'action="/match"', html)
            self.assertIn(b'href="/example"', html)
            self.assertEqual(self.request("GET", "/example"), (200, b"<h1>Kez</h1>"))
            self.assertEqual(self.request("GET", "/kez.png"), (200, b"image"))

    def test_entered_time_and_corrected_role_survive_saving_a_tactical_exercise(self):
        with patch("pro_replay.coach_app.OpenDota.match", return_value=scorecard()):
            form = {"match": 999, "slot": 132, "at": "10:00", "role": "5", "focus": "lane"}
            status, html = self.request("POST", "/coach", form=form)
            self.assertEqual(status, 200)
            self.assertIn("Позиция 5 выбрана игроком", html.decode())
            self.assertIn('name="at" value="10:00"', html.decode())
            self.assertIn("твоё убийство", html.decode().lower())
            status, saved = self.request("POST", "/practice", form=form)
            self.assertEqual(status, 200)
            self.assertIn("Задание сохранено", saved.decode())
            _, between = self.request("POST", "/coach", form={**form, "at": "00:10"})
            self.assertIn("нет размеченной драки", between.decode())
            self.assertNotIn('action="/practice"', between.decode())
            self.assertEqual(self.request("POST", "/coach", form={**form, "at": "10:60"})[0], 400)

    def test_local_forms_and_match_params(self):
        form = {"match": 999, "slot": 132}
        with patch("pro_replay.coach_app.OpenDota.match") as match:
            self.assertEqual(self.request("GET", "/match?match=999", headers={"Host": "another.local"})[0], 403)
            self.assertEqual(self.request("POST", "/coach", form=form, headers={"Origin": "https://example.com"})[0], 403)
            self.assertEqual(self.request("GET", "/coach?match=999&slot=132")[0], 405)
            self.assertEqual(self.request("GET", "/match?match=999&match=998")[0], 400)
            self.assertEqual(self.request("POST", "/coach", form={**form, "focus": "unknown"})[0], 400)
            self.assertEqual(self.request("POST", "/coach", form={**form, "slot": 89})[0], 400)
            match.assert_not_called()

    def test_missing_match_and_explicit_refresh_are_actionable(self):
        with patch("pro_replay.coach_app.OpenDota.match", side_effect=SourceError("missing", 404)):
            status, html = self.request("GET", "/match?match=999")
            self.assertEqual(status, 404)
            self.assertIn("Проверь ID матча", html.decode())
        with patch("pro_replay.coach_app.OpenDota.match", side_effect=[{"match_id": 999, "players": []}, scorecard()]) as match:
            status, empty = self.request("GET", "/match?match=999")
            self.assertEqual(status, 200)
            self.assertIn("Обновить из OpenDota", empty.decode())
            status, ready = self.request("GET", "/match?match=999&refresh=1")
            self.assertEqual(status, 200)
            self.assertIn("Выбери своего героя", ready.decode())
            self.assertEqual(match.call_count, 2)
