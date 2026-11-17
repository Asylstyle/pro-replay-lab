import json
from http.client import HTTPConnection
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from pro_replay.coach_app import make_server
from pro_replay.coach_report import decisions_section
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
        (self.folder / "coach-decisions.html").write_bytes(b"<h1>3 decisions</h1>")
        (self.folder / "patch-reference-example.html").write_bytes(b"<h1>Anti-Mage economy</h1>")
        (self.folder / "coach-kez.png").write_bytes(b"image")
        with patch("pro_replay.coach_app.EXAMPLES", self.folder):
            status, html = self.request("GET", "/")
            self.assertEqual(status, 200)
            self.assertIn(b'action="/match"', html)
            self.assertIn(b'href="/example"', html)
            self.assertIn(b'href="/example-decisions"', html)
            self.assertIn(b'href="/example-economy"', html)
            self.assertEqual(self.request("GET", "/example-economy"), (200, b"<h1>Anti-Mage economy</h1>"))
            self.assertEqual(self.request("GET", "/example-decisions"), (200, b"<h1>3 decisions</h1>"))
            self.assertEqual(self.request("GET", "/example"), (200, b"<h1>Kez</h1>"))
            self.assertEqual(self.request("GET", "/kez.png"), (200, b"image"))

    def test_browser_hero_click_loads_full_replay_and_renders_check_results(self):
        from contextlib import contextmanager
        from copy import deepcopy
        from pro_replay.coaching import make_session
        from test_match_decisions import decision

        captured = []
        @contextmanager
        def evidence(raw, cache):
            captured.append(raw["match_id"])
            yield {"match_id": raw["match_id"]}, ""

        native_make = make_session
        def session_with_checked_decision(*args, **kwargs):
            self.assertIsNotNone(kwargs.pop("timeline"))
            session = native_make(*args, **kwargs)
            sample = decision(590, 640, "Hex")
            sample.update(kind="focus", time=610, event="Observed death", review_from=598, evidence="Observed",
                          why="Interrupt", action="Use Hex", check="",
                          verification={"status":"verified", "time":600, "tick":18000, "reason":"Same frame.",
                                        "checks":[{"key":"vision", "label":"Обзор команды","state":"yes",
                                                   "detail":"Враг виден команде."}]})
            session.update(replay_checked=True, mistakes=[sample])
            failed = deepcopy(sample)
            failed["verification"].update(status="blocked", reason="Outside reach")
            session["excluded"] = [failed]
            return session
        with (patch("pro_replay.coach_app.OpenDota.match", return_value=scorecard()),
              patch("pro_replay.replay_files.replay_evidence", side_effect=evidence),
              patch("pro_replay.coaching.make_session", side_effect=session_with_checked_decision)):
            status, html = self.request("POST", "/coach", form={"match":999, "slot":132})
            self.assertEqual(status, 200)
            body = html.decode()
            self.assertIn("Можно было нажать на 10:00", body)
            self.assertIn("Обзор команды · Да", body)
            self.assertIn("Outside reach", body)
            self.assertEqual(captured, [999])

    def test_hero_button_submits_an_auto_report_without_manual_fields(self):
        with patch("pro_replay.coach_app.OpenDota.match", return_value=scorecard()) as fetch:
            status, roster = self.request("GET", "/match?match=999")
            self.assertEqual(status, 200)
            self.assertEqual(roster.count(b'class="hero-pick" type="submit" name="slot"'), 5)
            self.assertIn(b'<details class="manual-options">', roster)
            self.assertNotIn(b'type="radio"', roster)
            form = {"match": 999, "slot": 132}
            status, html = self.request("POST", "/coach", form=form)
            self.assertEqual(status, 200)
            self.assertLess(html.index(b'id="decisions"'), html.index(b'id="fight-timeline"'))
            self.assertIn(b'id="fight-590-640"', html)
            self.assertIn(b'name="focus" value="auto"', html)
            self.assertNotIn(b'name="at"', html)
            status, saved = self.request("POST", "/practice", form={**form, "focus": "auto"})
            self.assertEqual(status, 200)
            self.assertIn("Задание сохранено", saved.decode())
            fetch.assert_called_once_with(999)

    def test_report_distinguishes_target_threat_event_time_and_interval(self):
        entry = dict(time=600, start=590, end=640, event="Смерть героя", target="Mars", button="Black King Bar",
                     action="Проверить окно защиты", why="У соперника контроль", evidence="Куплен ранее",
                     check="Посмотри кулдаун", enemy_focus="Rubick", importance="Потерян союзник", recipient_side="self",
                     kind="self_defense", strength=2, review_from=590, pro_url="javascript:bad", pro_note="Сравнение")
        html = decisions_section([entry, dict(entry, time=None, enemy_focus="Mars", target="<Mars>", recipient_side="enemy")])
        for caption in ("Два главных момента", "Применить на себя", "Источник угрозы", "Кого фокусить",
                        "Событие на 10:00", "Интервал 9:50–10:40", "&lt;Mars&gt;", 'href="#fight-590-640"'):
            self.assertIn(caption, html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("decision-card", decisions_section([]))

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
