"""Turn observed match evidence into a practice session, without assigning blame."""

import gzip
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .analysis import select_player
from .accept_review import review_moments
from .macro_review import macro_review
from .mid_matchup import is_mid_laner, mid_matchup_review
from .mid_levels import level_review
from .mid_windows import mid_decision_windows
from .support_waves import support_wave_review
from .archive import normalize
from .coach_lessons import retrieve_lessons
from .coach_model import assess_player
from .patch_reference import load_reference, reference_for_player
from .fight_coach import analyze_fights
from .match_decisions import top_decisions
from .pro_purchases import PurchaseIndex, add_purchase_evidence
from .report import _clock, _item


FOCUSES = {"auto": "Выбрать по игре", "lane": "Линия и экономика",
           "fights": "Решения в драках", "build": "Покупки против драфта", "vision": "Обзор"}
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def parse_clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,4}:[0-5][0-9]", value):
        raise ValueError("Введи игровое время в формате ММ:СС, например 21:12.")
    minutes, seconds = value.split(":")
    return 60 * int(minutes) + int(seconds)


def json_file(path):
    opener = gzip.open if Path(path).suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig") as stream:
        return json.load(stream)


def replay(archive, api, mid, *, local=None, refresh=False):
    db = archive.db
    db.execute("CREATE TABLE IF NOT EXISTS coach_replays (match_id INTEGER PRIMARY KEY, raw BLOB NOT NULL)")
    if local is None and not refresh:
        cached = db.execute("SELECT raw FROM coach_replays WHERE match_id=?", (mid,)).fetchone()
        if cached:
            return json.loads(gzip.decompress(cached[0]))
    raw = json_file(local) if local else api.match(mid)
    if not isinstance(raw, dict) or raw.get("match_id") != mid or not isinstance(raw.get("players"), list):
        raise ValueError("В карточке должны быть указанный ID матча и состав игроков.")
    with db:
        db.execute("INSERT OR REPLACE INTO coach_replays VALUES (?,?)",
                   (mid, gzip.compress(json.dumps(raw, separators=(",", ":")).encode(), compresslevel=2)))
    return raw


def directory(archive):
    # The bundled public hero directory also works for a first offline import.
    heroes = {h["id"]: h for h in json_file(EXAMPLES / "coach-heroes.json")}
    for hid, unit, name in archive.db.execute("SELECT id,unit,title FROM heroes"):
        heroes[hid] = {"name": unit, "localized_name": name}
    return heroes


def make_card(topic, title, time, evidence, review, drill, check, query):
    return dict(topic=topic, title=title, time=time, evidence=evidence, review=review,
                drill=drill, check=check, query=query)


def lane_card(row, assessment, position):
    snapshots = {p["time"]: p for p in row["detail"]["checkpoints"]}
    a, b = snapshots.get(600), snapshots.get(1200)
    if not a or not b:
        return None
    field, unit = ("xp", "опыта") if position in (4, 5) else ("lh", "добиваний")
    if a.get(field) is None or b.get(field) is None:
        return None
    gain = b[field] - a[field]
    if gain < 0:
        return None
    text = f"За десять минут — {gain} {unit}. На 10:00 было {a[field]}, на 20:00 — {b[field]}."
    metric = next((m for m in assessment.get("metrics", []) if m["key"] == field), None)
    if metric and metric["gap"] is not None:
        size = abs(metric["gap"])
        if size <= metric["mae"]:
            text += (f" Разница с ориентиром патча ({size:.0f}) укладывается в среднюю ошибку "
                     f"модели на проверке ({metric['mae']:.0f}).")
        else:
            relative = "ниже" if metric["gap"] < 0 else "выше"
            text += f" На 20:00 результат {relative} ориентира на {size:.0f} при средней ошибке {metric['mae']:.0f}."
    training = ("Выбери, где получить опыт, сохраняя безопасную дистанцию до своей команды."
                if position in (4, 5) else "Перед уходом на следующий спот реши, какую волну успеешь забрать после него.")
    return make_card("lane", "Маршрут между 10-й и 20-й минутами", "10:00–20:00", text,
                     "Прокрути этот отрезок со своим обзором. Отметь один переход и то, что ты получил в конце пути.",
                     training, f"После следующей карты сравни прирост {unit} между этими же минутами и разбери один переход.",
                     ("маршрут опыт волна ротация поддержка обзор" if position in (4, 5) else
                      f"{row.get('hero_name', '')} маршрут фарма между волной и лесом переходы крипов"))


def fight_card(row):
    fights = row["detail"].get("fight_review", {}).get("intervals", [])
    involved = [f for f in fights if any((f.get(k) or 0) > 0 for k in ("damage", "healing", "deaths", "buybacks"))
                or any(f.get(k) for k in ("abilities", "items", "kills", "events"))]
    if not involved:
        return None
    with_deaths = [f for f in involved if (f.get("deaths") or 0) > 0]
    fight = max(with_deaths or involved, key=lambda f: (f["team"].get("deaths") or 0, f.get("damage") or 0))
    start, end = fight["start"], fight["end"]
    deaths = fight["deaths"]
    evidence = []
    if deaths is not None:
        evidence.append(f"Твои смерти в этом интервале — {deaths}.")
    if fight["damage"] is not None:
        team_damage = fight["team"]["damage"]
        total = f" из {team_damage} у команды" if team_damage is not None else ""
        evidence.append(f"Твой урон героям — {fight['damage']}{total}.")
    if fight.get("healing"):
        evidence.append(f"Лечение в этой драке — {fight['healing']}.")
    spells = sorted((fight.get("abilities") or {}).items(), key=lambda x: -x[1])
    if spells:
        key, n = spells[0]
        prefix = row["detail"]["fight_review"].get("hero_prefix", "")
        name = key.removeprefix(prefix + "_").replace("_", " ").title()
        evidence.append(f"{name} применена {n} раз.")
    if not evidence and fight.get("kills"):
        evidence.append("Герои, которых ты убил в этой драке — " + ", ".join(fight["kills"]) + ".")
    if not evidence:
        return None
    return make_card("fights", "Решение перед дракой", f"{_clock(start)}–{_clock(end)}", " ".join(evidence),
                     f"Начни просмотр с {_clock(max(0, start-10))} со стороны своей команды. Где находился ближайший союзник и кого из врагов было видно перед входом?",
                     "Перед двумя следующими драками выбери цель и назови способность врага, которую надо дождаться или избежать.",
                     "После игры найди оба эпизода. Для каждого отметь цель, угрозу и свой первый каст.",
                     f"командная драка выбор цели позиционирование первый каст {row.get('hero_name', '')} "
                     + " ".join(key.replace("_", " ") for key, _ in spells[:3]))


def build_card(row):
    build = row["detail"].get("build") or {}
    findings = build.get("findings", [])
    for finding in findings:
        checks = finding.get("checks") or []
        if checks:
            ex = checks[0]
            item = _item(ex["item"])
            evidence = f"В окне {_clock(ex['start'])}–{_clock(ex['end'])} у соперников записаны " + ", ".join(ex["threats"]) + "."
            stamp = next((t["purchase"] for t in finding.get("timing", []) if t["item"] == ex["item"]), None)
            if stamp is not None:
                evidence += f" Покупка {item} зарегистрирована на {_clock(stamp)}."
            return make_card("build", f"Проверь решение купить {item}", _clock(ex["start"]), evidence,
                             "Посмотри повтор со своего обзора. На кого пошёл вражеский каст и был ли нужный предмет доступен в этот момент?",
                             "Перед первой крупной покупкой назови одну угрозу из драфта и способ ответить на неё предметом или действием команды.",
                             "Найди первую драку после покупки и проверь выбранный ответ на реальном касте.",
                             f"покупка предмета против драфта тайминг драки {item} {row.get('hero_name', '')} "
                             + " ".join(ex["threats"]))
    purchases = [p for p in row.get("core_items", []) if p["time"] > 0]
    if not purchases:
        return None
    p = next((p for p in purchases if p["key"] not in ("power_treads","phase_boots","arcane_boots","tranquil_boots")), purchases[0])
    threats = ", ".join(build.get("enemies") or [])
    fact = f"В журнале {_item(p['key'])} появился на {_clock(p['time'])}." + (f" Драфт соперника — {threats}." if threats else "")
    return make_card("build", f"Задача для {_item(p['key'])}", _clock(p["time"]), fact,
                     "Какую задачу решала эта покупка на момент заказа? Проверь, успел ли ты использовать её преимущество до следующего большого события.",
                     "До заказа предмета сформулируй его задачу и отметь время доставки.",
                     "При разборе найди первый подходящий эпизод после доставки и проверь результат.",
                     f"покупка предмета билд тайминг {_item(p['key'])} {row.get('hero_name', '')} {threats}")


def vision_card(row):
    wards = [e for e in row.get("timeline", []) if e["kind"] == "observer"]
    if not wards:
        return None
    ward = next((e for e in wards if e["time"] >= 600), wards[0])
    return make_card("vision", "Какую информацию дал Observer Ward", _clock(ward["time"]),
                     f"На {_clock(ward['time'])} записана установка Observer Ward.",
                     "Переключись на обзор своей команды. Какой подход открывал вард и какое решение стало доступно союзникам?",
                     "При установке двух следующих Observer Ward сформулируй, какое командное действие каждый из них прикрывает.",
                     "Проверь обе точки на повторе. Запиши, видел ли вард соперника до выбранного действия.",
                     "обзор варды вижен саппорт контроль карты ward vision map support")


def sessions(archive, row, role):
    db = archive.db
    db.execute("""CREATE TABLE IF NOT EXISTS coaching_sessions(
        match_id INTEGER, slot INTEGER, account INTEGER, role INTEGER, population TEXT, mode INTEGER,
        started INTEGER, stats TEXT NOT NULL, PRIMARY KEY(match_id,slot))""")
    db.execute("""CREATE TABLE IF NOT EXISTS coaching_tasks(
        match_id INTEGER, slot INTEGER, topic TEXT NOT NULL, drill TEXT NOT NULL, check_text TEXT NOT NULL,
        PRIMARY KEY(match_id,slot))""")
    population = "pro" if row.get("league_id") else f"lobby-{row.get('lobby_type')}"
    points = {p["time"]: p for p in row["detail"]["checkpoints"]}
    stats = {}
    for key in ("lh", "gold", "xp"):
        a, b = (points.get(t, {}).get(key) for t in (600, 1200))
        stats[key] = b - a if a is not None and b is not None and b >= a else None
    earlier = []
    previous = None
    saved = db.execute("SELECT topic, drill, check_text FROM coaching_tasks WHERE match_id=? AND slot=?",
                       (row["match_id"], row["player_slot"])).fetchone()
    if row.get("account_id") and role in range(1, 6) and row.get("start_time") and row.get("game_mode") is not None:
        params = (row["account_id"], role, population, row["game_mode"], row["start_time"], row["match_id"])
        result = db.execute("""SELECT match_id, stats FROM coaching_sessions WHERE account=? AND role=?
             AND population=? AND mode=? AND started<? AND match_id<>? ORDER BY started DESC LIMIT 5""", params)
        earlier = [(mid, json.loads(body)) for mid, body in result]
        previous = db.execute("""SELECT s.match_id, t.drill, t.check_text FROM coaching_sessions s
            JOIN coaching_tasks t USING(match_id,slot) WHERE account=? AND role=? AND population=?
            AND mode=? AND started<? AND s.match_id<>? ORDER BY started DESC LIMIT 1""", params).fetchone()
    with db:
        db.execute("INSERT OR REPLACE INTO coaching_sessions VALUES (?,?,?,?,?,?,?,?)",
                   (row["match_id"], row["player_slot"], row.get("account_id"), role, population,
                    row.get("game_mode"), row.get("start_time"), json.dumps(stats)))
    return {"actual": stats, "prior": earlier, "identified": bool(row.get("account_id")),
            "saved": saved[0] if saved else None, "saved_content": saved[1:] if saved else None, "previous": previous}


def save_exercise(archive, session):
    if not session["cards"]:
        raise ValueError("Для этой карты пока нет упражнения.")
    card, row = session["cards"][0], session["row"]
    with archive.db:
        archive.db.execute("INSERT OR REPLACE INTO coaching_tasks VALUES (?,?,?,?,?)",
                           (row["match_id"], row["player_slot"], card["topic"], card["drill"], card["check"]))
    session["history"]["saved"] = card["topic"]
    session["history"]["saved_content"] = card["drill"], card["check"]


def selected_fight(fights, at):
    if at is not None:
        matches = [f for f in fights if f["start"] <= at <= f["end"]]
        chosen = min(matches, key=lambda f: abs(f["anchor"] - at), default=None)
        if chosen and chosen["events"]:
            event = min(chosen["events"], key=lambda e: (abs(e["time"] - at), e["kind"] == "ally_death"))
            chosen.update(anchor=event["time"], anchor_kind=event["kind"], anchor_text=event["text"])
        return chosen
    return max(fights, key=lambda f: (bool(f["threats"]), f["anchor_kind"] == "death",
                                      bool(f["items"]), f["anchor_kind"] != "interval"), default=None)


def tactical_card(fight, row):
    spells = " ".join(t["ability"] for t in fight["threats"][:3])
    personal = [i for i in fight["items"] if i["status"] in ("used", "prior_purchase")]
    gear = " ".join(i["name"] for i in (personal or fight["items"])[:2])
    concepts = {"target": "контроль инициация", "pierce": "позиционирование цель инициация",
                "magic_burst": "магический урон защита сейв", "mobility": "удержать цель контроль",
                "silence": "сайленс развеивание", "vision": "обнаружение обзор",
                "physical_pressure": "физический урон сейв", "area": "позиционирование дистанция"}
    topic = concepts.get(fight["threats"][0]["category"], "") if fight["threats"] else ""
    drills = {1: "Перед входом выбери доступную цель для атак и назови способность, которая может их прервать.",
              2: "Перед выходом на цель назови свой способ входа и то, как выйдешь после первого прокаста.",
              3: "Перед инициацией проверь, успевает ли ближайший союзник продолжить твой контроль.",
              4: "До начала драки выбери цель и реши, чей контроль продолжишь или какой каст прервёшь.",
              5: "До начала драки выбери союзника, которому может понадобиться спасение, и точку в радиусе помощи."}
    title = "Решение в драке"
    if fight["threats"]:
        title += " против " + fight["threats"][0]["hero"]
    return make_card("fights", title, _clock(fight["anchor"]),
                     " ".join([fight["anchor_text"].rstrip(".") + ".", *fight["observations"][:2]]),
                     fight["action"],
                     drills.get(fight["position"], "Перед похожей дракой назови первую угрозу и выбери момент входа."),
                     f'Сравни свой следующий бой с эпизодом на {_clock(fight["anchor"])}. {fight["check"]}',
                     f"командная драка {topic} {row.get('hero_name', '')} {spells} {gear}")


def make_session(archive, raw, slot, model, lesson_model, focus="auto", *, at=None, position=None, purchase_index=None,
                 timeline=None, replay_message=None, reference_path=None):
    if focus not in FOCUSES or not select_player(raw, None, slot):
        raise ValueError("Проверь выбранную тему и слот игрока.")
    if at is not None and (type(at) is not int or at < 0 or (raw.get("duration") is not None and at > raw["duration"])):
        raise ValueError("Выбранное время выходит за пределы матча.")
    if position is not None and (type(position) is not int or not 1 <= position <= 5):
        raise ValueError("Выбери игровую позицию от 1 до 5.")
    heroes = directory(archive)
    row = archive.with_patch_versions([normalize(raw, None, heroes, slot=slot)])[0]
    assessment = assess_player(model, raw, slot)
    role_override = position
    position = position or row["detail"]["build"].get("position")
    reference = reference_for_player(load_reference(reference_path), raw, slot, position)
    fights = analyze_fights(raw, slot, heroes, position=position)
    if purchase_index is None:
        purchase_index = PurchaseIndex.load()
    for fight in fights:
        add_purchase_evidence(fight, row, position, purchase_index)
        query = tactical_card(fight, row)["query"]
        fight["lessons"] = retrieve_lessons(lesson_model, query, position=position, limit=1, topic="fights")
    mistakes, excluded = [], []
    if focus == "auto" and at is None:
        reviewer, extra = None, []
        if timeline is not None:
            from .replay_choices import replay_choices
            from .replay_verification import ReplayVerifier
            reviewer = ReplayVerifier(timeline, match_id=raw["match_id"], start_time=raw.get("start_time"),
                                      hero_ids=[p.get("hero_id") for p in raw.get("players", []) if isinstance(p, dict)],
                                      game_mode=raw.get("game_mode"), patch_version=timeline.get("patch_version"))
            extra_fights, extra = replay_choices(timeline, raw, slot, heroes, fights, position)
            fights = sorted([*fights, *extra_fights], key=lambda f: (f["start"], f["end"]))
        mistakes = top_decisions(raw, slot, heroes, fights, row["detail"]["fight_review"]["intervals"],
                                 reviewer=reviewer, extra=extra, excluded=excluded)
    selected = selected_fight(fights, at)
    decision = tactical_card(selected, row) if selected else (fight_card(row) if at is None else None)
    if mistakes:
        first = mistakes[0]
        selected = next(f for f in fights if (f["start"], f["end"]) == (first["start"], first["end"]))
        stamp_time = first.get("verification", {}).get("time", first["time"])
        stamp = _clock(stamp_time) if stamp_time is not None else f'{_clock(first["start"])}–{_clock(first["end"])}'
        decision = make_card("fights", first["button"] + " · " + first["target"], stamp, first["evidence"],
                             first["why"], first["action"], first["check"],
                             f'командная драка {first["button"]} {first["target"]} {first["why"]}')
    if at is not None:
        focus = "fights"
    if timeline is not None and not mistakes and focus == "auto" and at is None:
        decision = None
    candidates = [lane_card(row, reference, position), decision, build_card(row), vision_card(row)]
    cards = {c["topic"]: c for c in candidates if c}
    # A descriptive forecast gap is a replay prompt only when larger than the
    # held-out error. Supports never get a last-hit quota.
    metric = "xp" if position in (4,5) else "lh"
    shortfall = not reference.get("in_reference") and any(m["key"] == metric and m.get("actual") is not None and m.get("expected") is not None
                    and m.get("mae") is not None and m["expected"] - m["actual"] > m["mae"]
                    for m in reference.get("metrics", []))
    preferred = (["fights"] if mistakes else []) + ([focus] if focus != "auto" else []) + (["lane"] if shortfall else [])
    preferred += ["fights", "vision" if position in (4,5) else "build", "lane", "build", "vision"]
    ordered = [cards[k] for k in dict.fromkeys(preferred) if k in cards][:3]
    if at is not None and not selected:
        ordered = []
    for card in ordered:
        hits = retrieve_lessons(lesson_model, card["query"], position=position, limit=2, topic=card["topic"])
        card["lessons"] = [h for h in hits if urlsplit(h["url"]).scheme == "https"]
    matchup = mid_matchup_review(raw, slot, role_override, patch=row.get("patch_version"))
    return {"row": row, "assessment": assessment, "patch_reference": reference, "cards": ordered, "focus": focus, "mistakes": mistakes,
            "accept_moments": review_moments(raw, slot, mistakes, at=at, position=position),
            "macro": macro_review(raw, slot, heroes, timeline),
            "mid_matchup": matchup, "mid_levels": level_review(raw, matchup, timeline),
            "mid_windows": mid_decision_windows(raw, slot, matchup, timeline),
            "support_waves": support_wave_review(raw, slot, position, timeline),
            "replay_checked": timeline is not None, "replay_message": replay_message, "excluded": excluded,
            "fights": fights, "selected_fight": selected, "at": at, "position": position, "role_override": role_override,
            "requested_available": focus == "auto" or focus in cards,
            "history": sessions(archive, row, position)}


def coach_by_id(archive, raw, slot, model, lessons, focus, *, cache, at=None, position=None, purchase_index=None, reference_path=None):
    options = dict(at=at, position=position, purchase_index=purchase_index, reference_path=reference_path)
    player = select_player(raw, None, slot) or {}
    requested_mid_lane = focus == "lane" and (position == 2 or (position is None and is_mid_laner(player)))
    if (focus != "auto" and not requested_mid_lane) or at is not None:
        return make_session(archive, raw, slot, model, lessons, focus, **options)
    from .replay_files import replay_evidence
    with replay_evidence(raw, cache) as (timeline, message):
        return make_session(archive, raw, slot, model, lessons, focus, timeline=timeline, replay_message=message, **options)
