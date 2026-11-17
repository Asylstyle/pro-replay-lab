"""Team objectives, smoke casts and pre-decision map evidence."""

from math import hypot

from .decision_events import _roster
from .fight_review import SLOTS, _objective
from .replay_verification import ReplayVerifier, _button, _control, _num, _ready
from .report import _clock, _h, _item

# Valve item 188 on 7.41f. The larger scouting distance below is a review
# heuristic, not the item's application radius or an estimate of enemy reach.
SMOKE_RADIUS = 1200
SCOUT_DISTANCE = 3500
POWER_ITEMS = {
    "blink": "Какую цель теперь можно достать первым прыжком? Кто успеет продолжить инициацию?",
    "black_king_bar": "Какой контроль теперь можно пережить и какая вражеская кнопка всё ещё опасна?",
    "manta": "Какой эффект можно снять и на какую волну безопасно отправить иллюзии?",
    "force_staff": "Кого можно вывести из опасности и будет ли у него безопасная точка приземления?",
    "glimmer_cape": "Кого можно прикрыть от прокаста? Где у соперника обнаружение?",
    "orchid": "На ком сайленс откроет окно для убийства до диспела или ответа союзников?",
    "sheepstick": "Кого команда успеет добить за контроль и какая цель видна до входа?",
    "desolator": "Хватит ли волны и обзора, чтобы превратить дополнительный физический урон в башню или Roshan?",
    "diffusal_blade": "Есть ли герой без надёжного отхода, к которому можно подойти вместе с союзником?",
    "maelstrom": "Какую волну или лагерь теперь можно зачистить быстрее до следующего командного действия?",
}


def _power_items(player, slot, events):
    result = []
    seen = set()
    for p in sorted((p for p in player.get("purchase_log") or () if isinstance(p, dict)
                     and type(p.get("time")) is int and p["time"] >= 0 and isinstance(p.get("key"), str)),
                    key=lambda p: p["time"]):
        key, t = p["key"], p["time"]
        if key not in POWER_ITEMS or key in seen:
            continue
        seen.add(key)
        cast = min((e["time"] for e in events if isinstance(e, dict) and e.get("kind") == "cast"
                    and e.get("actor_slot") == slot and e.get("button_key") == key
                    and type(e.get("time")) is int and e["time"] >= t), default=None)
        result.append((t, key, cast))
    return result


def _objectives(raw, members):
    names = {unit: name for _, unit, name in members.values() if unit}
    by_slot = {s: name for s, (_, _, name) in members.items()}
    result = set()
    for e in raw.get("objectives") or ():
        if not isinstance(e, dict) or type(e.get("time")) is not int or e["time"] < 0:
            continue
        kind = e.get("type")
        side = {2: "Radiant", 3: "Dire"}.get(e.get("team"))
        if kind == "CHAT_MESSAGE_MINIBOSS_KILL":
            label = f"Tormentor погиб · {side}" if side else "Tormentor погиб"
        elif kind == "CHAT_MESSAGE_AEGIS_STOLEN":
            label = "Перехват Aegis"
        elif kind == "building_kill" and e.get("key") in ("npc_dota_goodguys_fort", "npc_dota_badguys_fort"):
            label = "Упал Ancient " + ("Radiant" if "goodguys" in e["key"] else "Dire")
        else:
            label = _objective(e, names, by_slot)
        if label:
            result.add((e["time"], label))
    return sorted(result)


def _journal_smokes(members, allies):
    buys, uses = [], []
    for slot in allies:
        p, _, name = members[slot]
        purchases = p.get("purchase_log")
        if isinstance(purchases, list):
            buys.extend((e["time"], name) for e in purchases if isinstance(e, dict)
                        and type(e.get("time")) is int and e.get("key") == "smoke_of_deceit")
        counts = p.get("item_uses")
        if isinstance(counts, dict):
            value = counts.get("smoke_of_deceit", 0)
            if type(value) is int and value >= 0:
                uses.append(value)
    return sorted(buys), sum(uses) if len(uses) == len(allies) == 5 else None


def _snapshot(reader, second, members):
    frames = reader.window(second, second) if callable(reader.window) else reader.frames
    frame = next((f for f in frames if f.get("time") == second), None)
    return _states(frame, members)


def _states(frame, members):
    if not frame or type(frame.get("tick")) is not int or not isinstance(frame.get("heroes"), dict):
        return {}
    states = {}
    for slot, (player, _, _) in members.items():
        h = frame["heroes"].get(str(slot))
        if (isinstance(h, dict) and type(player.get("hero_id")) is int and h.get("hero_id") == player["hero_id"]
                and type(h.get("alive")) is bool):
            states[slot] = h
    return states


def _frames(reader, duration):
    if callable(reader.window):
        for start in range(60, duration, 12):
            yield from reader.window(start, min(start + 11, duration - 1))
    else:
        yield from (f for f in reader.frames if 60 <= f["time"] < duration)


def _smoke_windows(reader, duration, members, allies, casts):
    """Only use team knowledge available at each sampled second."""
    if not reader.patch_known or len(members) != 10 or type(duration) is not int or duration <= 0:
        return []
    windows = []
    team = "2" if allies[0] < 128 else "3"
    for frame in _frames(reader, duration):
        second = frame["time"]
        if windows and (windows[-1]["time"] // 600 == second // 600 or second - windows[-1]["time"] < 180):
            continue
        if any(0 <= second - t <= 45 for t, _ in casts):
            continue
        states = _states(frame, members)
        if len(states) != 10:
            continue
        opponents = [h for s,h in states.items() if s not in allies and h["alive"]]
        if any(not isinstance(h.get("visible_to"), dict) or type(h["visible_to"].get(team)) is not bool for h in opponents):
            continue
        seen = [s for s,h in states.items() if s not in allies and h["alive"] and h["visible_to"][team]]
        if not seen or any(not all(_num(states[s].get(k)) for k in ("x", "y"))
                           for s in [*allies, *seen] if states[s]["alive"]):
            continue
        for slot in allies:
            holder = states[slot]
            b = _button(holder, "smoke_of_deceit")
            if not holder["alive"] or type(b.get("charges")) is not int or b["charges"] < 1:
                continue
            checks = [*_ready(holder, b, True), _control(holder, True, False)]
            if any(c["state"] != "yes" for c in checks):
                continue
            def distance(h):
                return hypot(holder["x"] - h["x"], holder["y"] - h["y"])
            group = [members[s][2] for s in allies if s != slot and states[s]["alive"]
                     and distance(states[s]) <= SMOKE_RADIUS]
            closest = min(seen, key=lambda s: distance(states[s]))
            if len(group) < 2 or distance(states[closest]) < SCOUT_DISTANCE:
                continue
            windows.append({"time": second, "holder": members[slot][2], "allies": group,
                            "visible": len(seen), "closest": members[closest][2],
                            "distance": round(distance(states[closest]))})
            break
    return windows


def macro_review(raw, slot, heroes, timeline=None):
    members, _ = _roster(raw, heroes)
    if slot not in members:
        return None
    allies = sorted(s for s in members if (s < 128) == (slot < 128))
    objs = _objectives(raw, members)
    buys, uses = _journal_smokes(members, allies)
    reader = ReplayVerifier(timeline, match_id=raw.get("match_id"), start_time=raw.get("start_time"),
                            patch_version=timeline.get("patch_version") if isinstance(timeline, dict) else None)
    casts = set()
    if reader.valid:
        for e in timeline.get("events") or ():
            if (isinstance(e, dict) and e.get("kind") == "cast" and e.get("button_key") == "smoke_of_deceit"
                    and type(e.get("actor_slot")) is int and e["actor_slot"] in allies
                    and type(e.get("time")) is int and type(e.get("tick")) is int):
                casts.add((e["time"], e["actor_slot"]))
    casts = sorted(casts)
    intervals = [(e["start"], e["end"]) for e in raw.get("teamfights") or () if isinstance(e, dict)
                 and type(e.get("start")) is int and type(e.get("end")) is int and e["end"] >= e["start"]]
    fights = sorted({start for start, _ in intervals})
    aftermath = sorted([*objs, *((t, "Начало размеченной драки") for t in fights)])
    smoke_rows = []
    for i, (t, s) in enumerate(casts):
        until = min(t + 90, casts[i+1][0] - 1) if i+1 < len(casts) else t + 90
        following = next((event for event in aftermath if t < event[0] <= until), None)
        smoke_rows.append((t, members[s][2], following))
    opportunities = _smoke_windows(reader, raw.get("duration"), members, allies, casts) if reader.valid else []
    plans = []
    structures = set()
    for t, label in objs:
        tier = next((x for x in ("T1", "T2", "T3", "T4") if f"башня {x} " in label), None)
        if tier:
            if tier in structures:
                continue
            structures.add(tier)
            question = ("Какая союзная пачка подойдёт первой? Если соперник стянется, где безопаснее получить фарм?"
                        if tier in ("T1", "T2") else "Кто может выйти на возвышенность с волной и кто прикроет его при выкупе?")
        elif "Roshan" in label:
            question = "Кто может проверить яму? Сравни драку за Aegis с давлением на противоположной линии."
        elif "Tormentor" in label:
            question = "Кто может принять урон и какая линия останется без союзника, если команда свернёт к Tormentor?"
        else:
            continue
        pause = max(0, t - 30)
        context = []
        prior = max((end for _,end in intervals if 0 <= pause - end <= 90), default=None)
        if prior is not None:
            context.append(f"Последняя размеченная драка закончилась на {_clock(prior)}.")
        if reader.valid:
            states = _snapshot(reader, pause, members)
            if len(states) == 10:
                team = "2" if slot < 128 else "3"
                enemy = [h for s,h in states.items() if s not in allies and h["alive"]]
                context.append(f"Живых союзников — {sum(states[s]['alive'] for s in allies)} из пяти.")
                if all(isinstance(h.get("visible_to"), dict) and type(h["visible_to"].get(team)) is bool for h in enemy):
                    context.append(f"В командном обзоре соперников — {sum(h['visible_to'][team] for h in enemy)}.")
        plans.append((pause, question, " ".join(context), t, label))
    items = _power_items(members[slot][0], slot, (timeline.get("events") or []) if reader.valid else [])
    return {"objectives": objs, "power_items": items, "casts": smoke_rows,
            "buys": buys, "uses": uses, "replay": reader.valid, "windows": opportunities, "plans": plans}


def render_macro(data):
    if not data:
        return ""
    parts = ['<section class="panel" id="macro"><h2>Карта, объекты и смоки</h2>']
    objectives = data["objectives"]
    if objectives:
        items = "".join(f"<li><time>{_clock(t)}</time> {_h(label)}</li>" for t,label in objectives)
        parts.append(f"<details><summary>Хронология объектов ({len(objectives)})</summary><ol>{items}</ol></details>")
    else:
        parts.append("<p>В доступном журнале нет таймингов строений, Roshan, Aegis или Tormentor.</p>")
    if data["plans"]:
        parts.append("<h3>Паузы перед объектами</h3><p>Останови повтор за 30 секунд до события. "
                     "Выбери действие с обзором своей команды, затем открой результат.</p>")
        for pause, question, context, t, outcome in data["plans"]:
            parts.append(f"<details><summary><b>{_clock(pause)}</b> · {_h(question)}</summary>"
                         f"<p>{_h(context)} На {_clock(t)} в журнале: {_h(outcome)}.</p></details>")
    parts.append("<h3>Смоки твоей команды</h3>")
    if data["casts"]:
        rows = []
        for t, name, after in data["casts"]:
            outcome = (f"{_clock(after[0])} · {_h(after[1])}" if after else "В окне нет размеченной драки или объекта")
            rows.append(f"<tr><td>{_clock(t)}</td><td>{_h(name)}</td><td>{outcome}</td></tr>")
        parts.append('<div class="scroll"><table><thead><tr><th>Нажатие в повторе</th><th>Кто нажал</th>'
                     '<th>Ближайшее событие за следующие 90 секунд</th></tr></thead><tbody>'
                     + "".join(rows) + "</tbody></table></div>")
    elif data["replay"]:
        parts.append("<p>В прочитанных событиях повтора нет нажатий смока союзниками.</p>")
    else:
        parts.append("<p>Для точных секунд нажатия нужен журнал применений из повтора Valve.</p>")
    if data["uses"] is not None:
        parts.append(f'<p>Счётчик OpenDota за матч — {data["uses"]} применений командой.</p>')
    if data["buys"]:
        buys = ", ".join(f"{_clock(t)} · {_h(name)}" for t,name in data["buys"])
        parts.append(f"<details><summary>Когда союзники покупали смок</summary><p>{buys}</p></details>")
    if data["windows"]:
        parts.append("<h3>Когда можно было предложить смок</h3><ul>")
        for w in data["windows"]:
            parts.append(f'<li><strong>{_clock(w["time"])}</strong> · У {_h(w["holder"])} готов смок с зарядом. '
                         f'В радиусе нажатия {_h(", ".join(w["allies"]))}. В обзоре команды соперников — '
                         f'{w["visible"]}, ближайший из видимых — {_h(w["closest"])} в {w["distance"]} ед. '
                         'До нажатия назови цель выхода, покажи на карте безопасный маршрут и проверь линии.</li>')
        parts.append("</ul><p>Скрытые враги и вражеские вышки могут раскрыть выход. "
                     "Положение волн и готовность ключевых ультимейтов проверь в самом повторе.</p>")
    else:
        parts.append("<p>Для предложения нажать смок в конкретную секунду нужны одновременно готовый заряд, "
                     "живые союзники рядом и видимый соперник вдали. Подходящий кадр не подтверждён.</p>")
    parts.append('<p>Перед выходом сравни две цели: кого можно поймать и какой объект откроется, если соперник уйдёт. '
                 'Если драка за Roshan сейчас слабее, посмотри, какую линию можно вытолкнуть до его гибели. '
                 '<a href="https://www.youtube.com/watch?v=bu3SgQr0unU&amp;t=528s">Accept · смок MOUZ, 08:48</a> · '
                 '<a href="https://www.youtube.com/watch?v=bu3SgQr0unU&amp;t=561s">Линии перед Roshan, 09:21</a></p></section>')
    return "".join(parts)


def render_power_items(data):
    if not data or not data.get("power_items"):
        return ""
    rows = []
    for second, key, cast in data["power_items"]:
        application = f" Следующее записанное применение этой кнопки — {_clock(cast)}." if cast is not None else ""
        rows.append(f'<li><strong>{_clock(second)} · {_h(_item(key))}</strong>. {_h(POWER_ITEMS[key])}{application}</li>')
    return ('<section id="power-items"><h2>Твои предметные тайминги</h2><p>Время взято из журнала покупок. '
            'Проверь, когда предмет оказался в активном слоте, и сравни доступные действия до и после доставки.</p><ul>'
            + "".join(rows) + "</ul></section>")
