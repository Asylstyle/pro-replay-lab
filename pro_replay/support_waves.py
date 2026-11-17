"""Early lane decision reviews for the two supports, with separate game and video clocks."""

from html import escape
from math import dist

from .analysis import select_player
from .coach_model import _snapshot
from .impact import SLOTS
from .report import _clock
from .wave_tracks import coordinates, trajectory_windows


GUIDE = "https://www.youtube.com/watch?v=xPp-5wiI0nY"
CLIP_LABELS = {325: "Accept о цене раннего отвода", 498: "Accept о волне перед лотусом",
               926: "Accept о выборе между выходом через портал и мидом"}
# These are video examples and review checkpoints, not required camp contact times.
CHECKPOINTS = (
    (75, 325, "Остаться с кором или отвести своих крипов",
     "У какой башни встретятся следующие пачки? Если линия уходит от вашей башни, проверь, "
     "есть ли нейтралы в доступном лагере и выдержит ли кор размен один. Если оба условия "
     "выполнены, сравни полный и частичный отвод. Если враги могут вытеснить кора, "
     "что даст присутствие саппорта на линии?"),
    (150, 498, "Волна перед лотусом",
     "Кто первым освободится после этой волны? Посмотри, куда пойдёт следующая пачка, "
     "если сейчас отвести своих крипов или быстро добить вражеских. Какой вариант "
     "оставляет кору фарм и кому он даёт первым подойти к лотусу?"),
    (315, 926, "Куда вести следующую волну",
     "Если к обычной волне опасно подойти, посмотри, можно ли перехватить вражеских "
     "крипов за их первой башней и привести к своей следующей пачке в безопасное место. "
     "Где в последний раз видели второго противника и кто может перехватить тебя "
     "на обратном пути? Сравни этот маршрут с защитой кора или уходом на другую линию."),
)


def _lane_core(raw, player, slot):
    lane = player.get("lane")
    if lane not in (1, 3):
        return None
    cores = [p for p in raw["players"] if isinstance(p, dict)
             and type(p.get("player_slot")) is int and p["player_slot"] in SLOTS and p["player_slot"] != slot
             and (p["player_slot"] < 128) == (slot < 128)
             and p.get("lane") == lane and p.get("position_est") in (1, 2, 3)]
    return cores[0] if len(cores) == 1 else None


def _frame(timeline, second):
    provider = timeline.get("frame_window")
    frames = provider(second, second) if callable(provider) else timeline.get("frames") or []
    selected = [f for f in frames if isinstance(f, dict) and f.get("time") == second
                and type(f.get("tick")) is int and isinstance(f.get("heroes"), dict)]
    return selected[0]["heroes"] if len(selected) == 1 else {}


def _at_checkpoint(raw, player, core, timeline, slot, second):
    evidence = []
    alive = None
    heroes = _frame(timeline, second) if timeline else {}
    actor = heroes.get(str(slot))
    hero_id = player.get("hero_id")
    if (type(hero_id) is int and hero_id > 0 and isinstance(actor, dict) and actor.get("hero_id") == hero_id
            and type(actor.get("alive")) is bool):
        alive = actor["alive"]
        evidence.append("В кадре повтора твой герой жив." if alive else "В кадре повтора твой герой мёртв.")
        if alive and core:
            mate = heroes.get(str(core["player_slot"]))
            if (type(core.get("hero_id")) is int and core["hero_id"] > 0 and isinstance(mate, dict)
                    and mate.get("hero_id") == core["hero_id"] and mate.get("alive") is True):
                pos, other = coordinates(actor), coordinates(mate)
                if pos and other:
                    evidence.append(f"До союзного кора с той же линии {round(dist(pos, other))} ед. по прямой.")
    if core:
        minute = second // 60 * 60
        farm = _snapshot(core, minute, raw["duration"])
        if "lh" in farm:
            evidence.append(f"Добивания союзного кора по всей карте на {_clock(minute)} — {farm['lh']} (OpenDota).")
    return evidence, alive


def support_wave_review(raw, slot, position, timeline=None):
    """Describe a support's review windows. The position override takes priority over the estimate."""
    if not isinstance(raw, dict) or type(slot) is not int or slot not in SLOTS:
        return None
    if raw.get("game_mode") is not None and raw["game_mode"] not in (1, 2, 22):
        return None
    player = select_player(raw, None, slot)
    if not player:
        return None
    role = player.get("position_est") if position is None else position
    if type(role) is not int or role not in (4, 5):
        return None
    duration = raw.get("duration")
    if type(duration) is not int or duration < CHECKPOINTS[0][0]:
        return None
    if not (isinstance(timeline, dict) and timeline.get("format") == "pro-replay-truth-v1"
            and type(raw.get("match_id")) is int and raw["match_id"] > 0
            and type(timeline.get("match_id")) is int and timeline["match_id"] == raw["match_id"]):
        timeline = None
    core = _lane_core(raw, player, slot)
    windows = []
    for second, video, title, review in CHECKPOINTS:
        if second > duration:
            continue
        facts, alive = _at_checkpoint(raw, player, core, timeline, slot, second)
        windows.append({"time": second, "video_time": video, "title": title, "evidence": facts,
                        "review": "Отмотай до смерти и проверь, где кор мог встретить следующую волну."
                                  if alive is False else review})
    tracks = trajectory_windows(timeline, slot, player.get("hero_id"), duration)
    stacks = player.get("camps_stacked")
    return {"position": role, "side": "Radiant" if slot < 128 else "Dire", "checkpoints": windows,
            "trajectories": tracks, "stacks": stacks if type(stacks) is int and stacks >= 0 else None}


def render_support_waves(result):
    if not result:
        return ""
    rows = []
    for point in result["checkpoints"]:
        facts = " ".join(point["evidence"]) or "Время для ручной проверки в повторе."
        rows.append(f'<article class="episode"><h3><span class="stamp">{_clock(point["time"])}</span> '
                    f'{escape(point["title"])}</h3><p>{escape(facts)}</p><p>{escape(point["review"])}</p>'
                    f'<a href="{GUIDE}&amp;t={point["video_time"]}s">{CLIP_LABELS[point["video_time"]]} · '
                    f'{_clock(point["video_time"])} в видео</a></article>')
    observed = []
    for event in result["trajectories"]:
        start, end = _clock(event["time"]), _clock(event["end"])
        count = event["creeps"]
        if event["kind"] == "camp":
            detail = (f"Рядом с твоим героем нейтрал вышел навстречу союзной волне, затем вернулся "
                      f"в сторону исходной точки. С ним туда сместились союзные крипы ({count}).")
            question = "Сверь в повторе, кто взял агро и выгодно ли в это время было оставлять кора одного."
        else:
            detail = (f"Между первой и второй боковыми башнями противника ты и вражеские крипы ({count}) "
                      f"сместились вместе в сторону от линии башен.")
            question = "Посмотри на команды крипов и место встречи волн, чтобы отличить перетяжку от прохода рядом."
        observed.append(f'<li><strong>{start}–{end}</strong> {detail} {question}</li>')
    trail = ('<h3>Движение крипов в твоём повторе</h3><ul>' + "".join(observed) + '</ul>') if observed else ''
    stacks = (f'<p>Стаков, засчитанных OpenDota за весь матч, — {result["stacks"]}.</p>'
              if result["stacks"] is not None else '')
    return ('<section id="support-waves"><h2>Саппорт и волна крипов</h2>'
            f'<p>Позиция {result["position"]}, {result["side"]}. Открой эпизод с обзором своей команды. '
            'Для отвода своих крипов выведи нейтралов навстречу проходящей союзной пачке. '
            'Для перетяжки вражеской волны проверь, что крипы идут за тобой и у тебя есть путь к своей пачке. '
            'При стаке цель другая — освободить лагерь к появлению следующей группы нейтралов.</p>'
            + stacks + trail + "".join(rows)
            + '<p>Проверь место лагеря, положение обеих пачек и момент захвата цели '
              'на карте своего патча. Поминутный фарм и дистанция между героями дают контекст. '
              'Выполнение манёвра видно по движению крипов.</p></section>')
