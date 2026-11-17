"""A loopback-only browser entry point for the replay coach."""

from contextlib import closing
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .archive import Archive, normalize
from .coach_model import load_coach_model
from .coach_report import page, render_session
from .coaching import EXAMPLES, FOCUSES, coach_by_id, directory, json_file, parse_clock, replay, save_exercise
from .pro_purchases import PurchaseIndex
from .impact import SLOTS
from .match_report import render_match
from .report import _clock
from .source import OpenDota, SourceError


ENTRY_STYLE = """<style>
.coach-entry,.coach-picker{margin:2rem 0}
.coach-entry{display:grid;grid-template-columns:minmax(0,1fr) minmax(230px,.9fr);gap:2rem;align-items:center}
.coach-entry h1,.coach-picker h1{font-size:clamp(2.2rem,6vw,4.8rem);letter-spacing:-.045em;line-height:1.02;margin:.4em 0}
.coach-entry p,.coach-picker p{max-width:48ch}
.coach-entry img{display:block;width:100%;max-height:460px;object-fit:contain}
.match-field{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 18px}
.coach-entry input,.coach-picker select{font:inherit;padding:.7rem .8rem;border:1px solid #80919a;border-radius:5px;background:#121d23;color:#eff5f1}
.coach-entry button,.coach-picker button{font:inherit;font-weight:650;padding:.75rem 1.2rem;color:#152014;background:#d4ec80;border:0;border-radius:5px;cursor:pointer}
.coach-entry :focus-visible,.coach-picker :focus-visible{outline:3px solid #d4ec80;outline-offset:3px}
.coach-sides{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;margin:22px 0}
.coach-sides fieldset{margin:0;padding:8px;border:1px solid #41515a;min-width:0}
.coach-sides legend{font-weight:700;padding:0 6px}
.coach-picker .hero-pick{display:flex;width:100%;align-items:center;justify-content:space-between;gap:.7rem;min-height:52px;
padding:.65rem .8rem;text-align:left;background:transparent;color:var(--ink);border-radius:0;border-bottom:1px solid var(--line)}
.hero-pick:last-child{border-bottom:0}.hero-pick small{display:block;font-weight:400;overflow-wrap:anywhere}
.hero-pick::after{content:"→";color:var(--accent)}.coach-picker .hero-pick:hover,.coach-picker .hero-pick:focus-visible{background:var(--panel)}
.coach-picker select{margin:0 10px 6px 0}.manual-options{margin-top:15px}.manual-options summary{cursor:pointer}
.fight-inputs{display:flex;gap:.75rem 1.5rem;flex-wrap:wrap;margin:15px 0}
.fight-inputs label,.fight-inputs small{display:block}.fight-inputs select,.fight-inputs input{margin-top:4px}
@media(max-width:680px){.coach-entry{grid-template-columns:1fr}.coach-entry img{max-height:240px;order:-1}.coach-sides{grid-template-columns:1fr}}
</style>"""


def _home():
    return page("Тренер по Dota 2", ENTRY_STYLE + """
<div class="coach-entry"><div><h1>Разбери свою игру</h1>
<p>Введи номер матча и выбери героя. Тренер откроет повтор Valve и покажет до трёх моментов, когда кнопка была готова, команда видела врага и цель была в радиусе действия.</p>
<form action="/match" method="get"><label for="match-id">Номер матча</label><div class="match-field">
<input id="match-id" type="text" name="match" inputmode="numeric" pattern="[0-9]+" maxlength="19"
       placeholder="9086497898" required>
<button type="submit">Открыть состав</button></div></form>
<p><a href="/example-decisions">Пример автоматического разбора</a></p>
<nav><a href="/example-mid-rotation#mid-exchanges">Pangolier и Lion против Tinker</a> · <a href="/example-mid-invoker#mid-exchanges">Invoker против Puck · размен или крип</a> · <a href="/example-shadowraze#mid-windows">Койлы Shadow Fiend</a> · <a href="/example-macro#macro">Смоки и объекты Pudge</a> · <a href="/example-waves#support-waves">Отвод Snapfire</a></nav>
<details class="resources"><summary>Другие примеры</summary><nav><a href="/example-replay-support">Проверенные сейвы Snapfire</a><a href="/example-fight">Драка Mars</a>
<a href="/example-economy">Фарм Anti-Mage в сравнении с пабами 7 300+</a>
<a href="/example-mid#mid-matchup">Huskar против Necrophos по уровням</a>
<a href="/example-mid-tinker#mid-exchanges">Tinker против Pangolier · когда нажать Laser</a>
<a href="/example-mid-nukes#mid-windows">Размен и дальний крип на Rubick</a>
<a href="/example-mid-qop#mid-windows">Queen of Pain против Ember Spirit</a>
<a href="/example-support">Выбор предмета на Rubick</a><a href="/example">Посмотреть готовый разбор Kez</a></nav></details></div>
<img src="/kez.png" alt="Kez из Dota 2"></div>""")


def _match_form(raw, heroes):
    mid = raw["match_id"]
    by_slot = {}
    for p in raw["players"]:
        if not isinstance(p, dict) or type(p.get("player_slot")) is not int or p["player_slot"] not in SLOTS:
            continue
        slot = p["player_slot"]
        # Ambiguous slots cannot identify a single player in the replay.
        by_slot[slot] = None if slot in by_slot else p
    if not any(by_slot.values()):
        return page("Ожидаем состав", f'<h1>Состав матча #{mid} пока недоступен</h1>'
                    '<p>Когда OpenDota обработает карточку, здесь можно будет выбрать героя.</p>'
                    f'<p><a href="/match?match={mid}&amp;refresh=1">Обновить из OpenDota</a></p>'
                    '<p><a href="/">Выбрать другой матч</a></p>')
    sides = []
    for team, slots in (("Radiant", SLOTS[:5]), ("Dire", SLOTS[5:])):
        choices = []
        for slot in slots:
            p = by_slot.get(slot)
            if not p:
                continue
            hero = heroes.get(p.get("hero_id"), {}).get("localized_name") or f'Герой #{p.get("hero_id", "—")}'
            name = p.get("name") or p.get("personaname")
            caption = f'<small>{escape(str(name))}</small>' if name else ""
            choices.append(f'<button class="hero-pick" type="submit" name="slot" value="{slot}">'
                           f'<span><b>{escape(str(hero))}</b>{caption}</span></button>')
        sides.append(f'<fieldset><legend>{team}</legend>{"".join(choices) or "Состав недоступен"}</fieldset>')
    options = "".join(f'<option value="{key}">{escape(label)}</option>' for key, label in FOCUSES.items())
    roles = "".join(f'<option value="{i}">{i} · {label}</option>' for i, label in
                    enumerate(("Керри", "Мид", "Офлейн", "Поддержка", "Полная поддержка"), 1))
    intervals = [f for f in raw.get("teamfights") or [] if isinstance(f, dict) and
                 type(f.get("start")) is int and type(f.get("end")) is int and 0 <= f["start"] <= f["end"]]
    timings = ''.join(f'<option value="{_clock(f["start"])}">{_clock(f["start"])}–{_clock(f["end"])}</option>'
                      for f in sorted(intervals, key=lambda f: f["start"]))
    return page("Выбор игрока", ENTRY_STYLE + f'<div class="coach-picker"><a href="/">← Другой матч</a>'
                f'<h1>Выбери своего героя</h1><p>Матч #{mid} · <a href="/match?match={mid}&amp;refresh=1">Обновить из OpenDota</a></p>'
                '<form method="post" action="/coach">'
                f'<input type="hidden" name="match" value="{mid}">'
                '<details class="manual-options"><summary>Настроить разбор вручную</summary>'
                '<div class="fight-inputs"><label for="focus">Что разобрать'
                f'<select id="focus" name="focus">{options}</select></label><label for="role">Твоя позиция'
                f'<select id="role" name="role"><option value="">По данным матча</option>{roles}</select></label>'
                '<label for="fight-time">Время драки <small>По желанию, мин:сек</small>'
                '<input id="fight-time" list="intervals" name="at" type="text" placeholder="21:12" maxlength="7" '
                f'pattern="[0-9]{{1,4}}:[0-5][0-9]"></label><datalist id="intervals">{timings}</datalist></div>'
                f'</details><div class="coach-sides">{"".join(sides)}</div>'
                '<p id="replay-progress" role="status" hidden>Загружаю повтор Valve и проверяю действия героя. '
                'Первый разбор может занять несколько минут.</p></form></div>'
                '<script>document.querySelector(".coach-picker form").addEventListener("submit",()=>{'
                'if(document.querySelector("#focus").value==="auto"&&!document.querySelector("#fight-time").value)'
                'document.querySelector("#replay-progress").hidden=false;});</script>')


def _number(params, name):
    values = params.get(name, [])
    if len(values) != 1 or not values[0].isascii() or not values[0].isdecimal() or len(values[0]) > 19:
        raise ValueError("Проверь номер матча или выбранный слот.")
    value = int(values[0])
    if (name == "match" and not 0 < value < 2**63) or (name == "slot" and value not in SLOTS):
        raise ValueError("Проверь номер матча или выбранный слот.")
    return value


class CoachHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # A local browser session needs only the startup address in the console.
        pass

    def _send(self, status, content, mime="text/html; charset=utf-8"):
        body = content.encode("utf-8") if isinstance(content, str) else content
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _message(self, status, message):
        self._send(status, page("Разбор матча", f'<h1>Разбор матча</h1><p>{escape(message)}</p>'
                                '<p><a href="/">Выбрать другой матч</a></p>'))

    def _local(self):
        port = self.server.server_port
        hosts = {f"localhost:{port}", f"127.0.0.1:{port}"}
        if port == 80:
            hosts.update(("localhost", "127.0.0.1"))
        if self.headers.get("Host", "").lower() not in hosts:
            self._message(403, "Открой локальный адрес из окна программы.")
            return False
        origin = self.headers.get("Origin")
        if self.command == "POST" and ((origin and origin.lower() not in {"http://" + h for h in hosts})
                                       or self.headers.get("Sec-Fetch-Site") == "cross-site"):
            self._message(403, "Отправь форму из локального приложения.")
            return False
        return True

    def _run(self, path, params):
        if path == "/":
            return self._send(200, _home())
        if path == "/example":
            return self._send(200, (EXAMPLES / "coach-kez.html").read_bytes())
        if path == "/example-decisions":
            return self._send(200, (EXAMPLES / "coach-decisions.html").read_bytes())
        if path == "/example-economy":
            return self._send(200, (EXAMPLES / "patch-reference-example.html").read_bytes())
        samples = {"/example-macro": "coach-macro-smoke.html", "/example-mid": "coach-mid-matchup.html",
                   "/example-waves": "coach-support-waves.html", "/example-mid-nukes": "coach-mid-nukes.html",
                   "/example-shadowraze": "coach-mid-shadow-fiend.html", "/example-mid-qop": "coach-mid-qop.html",
                   "/example-mid-invoker": "coach-mid-invoker.html", "/example-mid-tinker":"coach-mid-tinker.html",
                   "/example-mid-rotation": "coach-mid-rotation.html"}
        if path in samples:
            return self._send(200, (EXAMPLES / samples[path]).read_bytes())
        if path == "/example-replay-support":
            return self._send(200, (EXAMPLES / "coach-replay-support.html").read_bytes())
        if path == "/example-fight":
            return self._send(200, (EXAMPLES / "coach-fight.html").read_bytes())
        if path == "/example-support":
            return self._send(200, (EXAMPLES / "coach-rubick.html").read_bytes())
        if path in ("/kez.png", "/coach-kez.png"):
            return self._send(200, (EXAMPLES / "coach-kez.png").read_bytes(), "image/png")
        if path not in ("/match", "/coach", "/detail", "/practice"):
            return self._message(404, "Такой страницы нет.")
        mid = _number(params, "match")
        slot = _number(params, "slot") if path != "/match" else None
        if path in ("/coach", "/practice"):
            focus = params.get("focus", ["auto"])
            if len(focus) != 1 or focus[0] not in FOCUSES:
                raise ValueError("Выбери одну из доступных тем.")
            times, roles = params.get("at", [""]), params.get("role", [""])
            if len(times) != 1 or len(roles) != 1 or roles[0] not in ("", "1", "2", "3", "4", "5"):
                raise ValueError("Выбери одну позицию и один тайминг.")
            at = parse_clock(times[0]) if times[0] else None
            role = int(roles[0]) if roles[0] else None
        archive = Archive(self.server.db_path)
        with closing(archive.db):
            refresh = path == "/match" and params.get("refresh") == ["1"]
            raw = replay(archive, OpenDota(match_metadata=True), mid, refresh=refresh)
            if path == "/match":
                return self._send(200, _match_form(raw, directory(archive)))
            if path == "/detail":
                row = normalize(raw, None, directory(archive), slot=slot)
                if not row:
                    raise ValueError("В матче нет выбранного героя.")
                row = archive.with_patch_versions([row])[0]
                return self._send(200, render_match(row, archive.personal_history(row), archive.references(),
                                                     raw_match=raw, reference_path=self.server.reference_path))
            model = load_coach_model(self.server.model_path)
            lessons = json_file(self.server.lessons_path)
            session = coach_by_id(archive, raw, slot, model, lessons, focus[0], at=at,
                                  cache=Path(self.server.db_path).parent / "replay-evidence",
                                  position=role, purchase_index=PurchaseIndex.load(self.server.fight_model_path) if self.server.fight_model_path else None,
                                  reference_path=self.server.reference_path)
            if path == "/practice":
                if not session["requested_available"]:
                    raise ValueError("Для выбранного упражнения недостаточно данных.")
                save_exercise(archive, session)
            detail_url = f"/detail?match={mid}&slot={slot}"
            return self._send(200, render_session(session, home_url="/", detail_url=detail_url))

    def _dispatch(self):
        if not self._local():
            return
        try:
            url = urlsplit(self.path)
            if url.scheme or url.netloc:
                raise ValueError("Открой страницу приложения.")
            form = url.query
            if self.command == "POST":
                if url.path not in ("/coach", "/practice"):
                    return self._message(404, "Такой формы нет.")
                length = int(self.headers.get("Content-Length", 0))
                if self.headers.get_content_type() != "application/x-www-form-urlencoded" or not 0 < length <= 1024:
                    raise ValueError("Некорректная форма разбора матча.")
                form = self.rfile.read(length).decode("utf8")
            elif url.path in ("/coach", "/practice"):
                return self._message(405, "Сначала выбери героя в составе матча.")
            self._run(url.path, parse_qs(form, keep_blank_values=True, max_num_fields=8))
        except SourceError as exc:
            if exc.status == 404:
                self._message(404, "OpenDota пока не нашёл эту карту. Проверь ID матча.")
            else:
                self._message(502, "OpenDota не вернул данные. Повтори попытку позже или открой готовый разбор.")
        except (ValueError, UnicodeError) as exc:
            self._message(400, str(exc))
        except (ConnectionResetError, BrokenPipeError):
            return
        except (OSError, sqlite3.Error):
            self._message(500, "Не удалось открыть локальные данные. Проверь папку проекта и путь к базе.")

    do_GET = _dispatch
    do_POST = _dispatch


def make_server(db_path, *, port=0, model_path=None, lessons_path=None, fight_model_path=None, reference_path=None):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Номер порта должен быть от 0 до 65535.")
    server = ThreadingHTTPServer(("127.0.0.1", port), CoachHandler)
    server.db_path = db_path
    server.model_path = model_path
    server.reference_path = reference_path
    server.fight_model_path = fight_model_path
    server.lessons_path = Path(lessons_path) if lessons_path else EXAMPLES / "coach-lessons-model.json"
    return server


def serve(db_path, *, port=0, open_browser=True, model_path=None, lessons_path=None, fight_model_path=None, reference_path=None):
    with make_server(db_path, port=port, model_path=model_path, lessons_path=lessons_path,
                     fight_model_path=fight_model_path, reference_path=reference_path) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"Тренер открыт по адресу {url}\nДля завершения нажми Ctrl+C.", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
