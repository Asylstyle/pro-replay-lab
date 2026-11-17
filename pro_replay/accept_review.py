"""Match annotated video lessons with observed moments in a client's own game."""

from functools import lru_cache
import json
from pathlib import Path

from .coach_model import _snapshot
from .events import event_data
from .fight_review import SLOTS
from .report import _clock, _h, _item
from .youtube_captions import video_id


@lru_cache(maxsize=1)
def _catalog():
    return json.loads(Path(__file__).with_name("accept_lessons.json").read_text(encoding="utf8"))


def _observations(raw, player, decisions):
    # The verifier already checked the button, caster, recipient and enemy on
    # the same pre-outcome frame. A combat-log timestamp alone is insufficient.
    for d in decisions:
        verification = d.get("verification") or {}
        if verification.get("status") != "verified" or not d.get("button") or not d.get("target"):
            continue
        when = verification.get("time")
        if type(when) is int and d.get("kind") in {"focus", "interrupt", "protect", "self_defense"}:
            yield ("verified_" + d["kind"], when, None, (d["start"], d["end"]), "Кадр повтора Valve",
                   f'В кадре выполнены проверенные условия для применения {d["button"]}. Цель — {d["target"]}.')

    purchases = player.get("purchase_log")
    for p in purchases if isinstance(purchases, list) else ():
        if isinstance(p, dict) and type(p.get("time")) is int and isinstance(p.get("key"), str):
            yield ("item_timing", p["time"], p["key"], None, "Журнал покупок OpenDota",
                   f'У выбранного героя в журнале появилась покупка {_item(p["key"])}.')

    objectives = raw.get("objectives")
    for event in event_data(raw if isinstance(objectives, list) else {**raw, "objectives": []},
                            None, player=player)["timeline"]:
        if event["kind"] in ("death", "roshan"):
            signal = "death_review" if event["kind"] == "death" else "objective_review"
            yield signal, event["time"], None, None, "Журнал событий OpenDota", event["detail"] + "."

    enemy_team = 3 if player["player_slot"] < 128 else 2
    for event in objectives if isinstance(objectives, list) else ():
        if (isinstance(event, dict) and event.get("type") == "CHAT_MESSAGE_ROSHAN_KILL"
                and event.get("team") == enemy_team and type(event.get("time")) is int):
            yield ("enemy_roshan", event["time"], None, None, "Журнал целей OpenDota",
                   "В журнале убийство Roshan засчитано сопернику.")

    duration = raw.get("duration")
    lane = _snapshot(player, 600, duration if type(duration) is int else None)
    if "lh" in lane and "gold" in lane:
        yield ("lane_economy", 600, None, None, "Поминутные показатели OpenDota",
               f'У героя {lane["lh"]} добиваний и {lane["gold"]} полученного золота на 10:00.')


def review_moments(raw, slot, decisions, lessons=None, *, at=None, position=None):
    """Return up to three distinct questions anchored to the selected player's game."""
    if type(slot) is not int or slot not in SLOTS:
        return []
    players = [p for p in raw.get("players") or [] if isinstance(p, dict)
               and type(p.get("player_slot")) is int and p["player_slot"] == slot]
    if len(players) != 1:
        return []
    player = players[0]
    observations = list(_observations(raw, player, decisions))
    duration = raw.get("duration")
    moments, used_times, used_sources = [], set(), set()
    for lesson in (_catalog() if lessons is None else lessons):
        if lesson.get("roles") and position not in lesson["roles"]:
            continue
        if lesson.get("hero_ids") and player.get("hero_id") not in lesson["hero_ids"]:
            continue
        source_id, source_second = video_id(lesson["video_id"]), lesson["video_second"]
        lead = lesson.get("lead_seconds", 0)
        if type(source_second) is not int or source_second < 0 or type(lead) is not int or not 0 <= lead <= 120:
            raise ValueError("Некорректный таймкод учебного эпизода.")
        if (source_id, source_second) in used_sources:
            continue
        for signal, when, item, interval, basis, fact in observations:
            if signal != lesson["signal"] or when < 0 or (type(duration) is int and when > duration):
                continue
            if "max_game_second" in lesson and when > lesson["max_game_second"]:
                continue
            if signal == "item_timing" and item not in lesson.get("item_keys", []):
                continue
            pause = max(0, when - lead)
            start, end = interval if interval is not None else (pause, when)
            if when in used_times or (at is not None and not min(start, pause) <= at <= end):
                continue
            moments.append({"time": when, "pause_time": pause, "basis": basis, "fact": fact,
                            "title": lesson["title"], "question": lesson["question"],
                            "criterion": lesson["criterion"], "source_note": lesson["source_note"],
                            "video_second": source_second,
                            "video_url": f"https://www.youtube.com/watch?v={source_id}&t={source_second}s"})
            used_times.add(when)
            used_sources.add((source_id, source_second))
            break
        if len(moments) == 3:
            break
    return moments


def render_review(moments):
    if not moments:
        return ""
    body = ['<section id="accept-review"><h2>Как прочитать эпизод</h2>',
            '<p>Поставь свой повтор на паузу в указанное время и включи обзор своей команды. '
            'Сформулируй решение, затем открой разбор.</p>']
    for m in moments:
        url = f'https://www.youtube.com/watch?v={video_id(m["video_url"])}&t={int(m["video_second"])}s'
        body.append(f'<article class="episode"><h3><span class="stamp">{_clock(m["pause_time"])} в твоей игре</span>'
                    f'{_h(m["title"])}</h3><p>{_h(m["question"])}</p>'
                    f'<details class="resources"><summary>Сверить решение</summary>'
                    f'<p><b>{_h(m["basis"])} · {_clock(m["time"])}</b><br>{_h(m["fact"])}</p>'
                    f'<p>{_h(m["criterion"])}</p><p>{_h(m["source_note"])} '
                    f'<a href="{_h(url)}">Разбор Accept · {_clock(m["video_second"])} в видео</a></p>'
                    '</details></article>')
    return "".join([*body, "</section>"])
