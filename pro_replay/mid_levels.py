"""Level leads and observed skill-point investments of the two lane opponents."""

from bisect import bisect_right
from html import escape

from .mid_spells import spell_specs
from .report import _clock


# Cumulative XP at each level; OpenDota dotaconstants, commit 84f63b28.
LEVEL_XP = (0, 240, 640, 1160, 1760, 2440, 3200, 4000, 4900, 5900, 7000, 8200,
            9500, 10900, 12400, 14000, 15700, 17500, 19400, 21400, 23600, 26000,
            28600, 31400, 34400, 38400, 43400, 49400, 56400, 63900)
LANE_END = 600


def _xp_samples(player):
    times, xp = player.get("times"), player.get("xp_t")
    if not isinstance(times, list) or not isinstance(xp, list) or len(times) != len(xp):
        return {}
    result = {}
    last_t, last_xp = -1, -1
    for t, value in zip(times, xp):
        if type(t) is not int or t < 0 or t > LANE_END:
            continue
        if t <= last_t or type(value) is not int or value < last_xp or value < 0:
            return {}
        result[t] = bisect_right(LEVEL_XP, value)
        last_t, last_xp = t, value
    return result


def _frames(timeline, duration):
    last = min(duration, LANE_END)
    provider = timeline.get("frame_window")
    if callable(provider):
        for t in range(0, last + 1, 20):
            yield from provider(t, min(t + 19, last))
    else:
        frames = (f for f in timeline.get("frames") or () if isinstance(f, dict)
                  and type(f.get("time")) is int and 0 <= f["time"] <= last)
        yield from sorted(frames, key=lambda f: f["time"])


def _valid_hero(frame, player):
    units = frame.get("heroes")
    actor = units.get(str(player["slot"])) if isinstance(units, dict) else None
    return (actor if isinstance(actor, dict) and actor.get("hero_id") == player["id"]
            and type(actor.get("level")) is int and 1 <= actor["level"] <= 30 else None)


def _ranks(actor, specs):
    buttons = actor.get("buttons")
    if not isinstance(buttons, dict):
        return {}
    return {key: b["level"] for key, b in buttons.items() if key in specs and isinstance(b, dict)
            and b.get("kind") == "ability" and type(b.get("level")) is int and 0 <= b["level"] <= 30}


def level_review(raw, matchup, timeline=None):
    if not matchup or matchup.get("status") != "available":
        return None
    players = (matchup["self"], matchup["opponent"])
    roster = {p["player_slot"]: p for p in raw.get("players") or () if isinstance(p, dict) and "player_slot" in p}
    samples = [_xp_samples(roster[p["slot"]]) for p in players]
    journal = [(t, samples[0][t], samples[1][t]) for t in sorted(samples[0].keys() & samples[1].keys())]
    result = {"runs": [], "upgrades": [], "samples": 0, "journal": journal}
    if (not isinstance(timeline, dict) or timeline.get("format") != "pro-replay-truth-v1"
            or type(raw.get("match_id")) is not int or timeline.get("match_id") != raw["match_id"]
            or timeline.get("patch_version") not in (None, matchup["patch"]) or type(raw.get("duration")) is not int):
        return result
    side = "2" if players[0]["slot"] < 128 else "3"
    specs = [spell_specs(p["id"]) for p in players]
    last_time, prior_ranks = None, None
    for frame in _frames(timeline, raw["duration"]):
        t = frame.get("time")
        if type(t) is not int or type(frame.get("tick")) is not int:
            continue
        pair = [_valid_hero(frame, player) for player in players]
        if not all(pair) or (last_time is not None and t <= last_time):
            continue
        levels = [a["level"] for a in pair]
        ranks = [_ranks(a, spec) for a, spec in zip(pair, specs)]
        continuous = last_time is not None and t == last_time + 1
        if continuous:
            for i, current in enumerate(ranks):
                for key, rank in current.items():
                    if key in prior_ranks[i] and rank == prior_ranks[i][key] + 1:
                        result["upgrades"].append({"time": t, "who": i, "level": levels[i],
                            "key": key, "skill": specs[i][key]["name"], "rank": rank})
        if levels[0] != levels[1] and all(a.get("alive") is True for a in pair):
            runs = result["runs"]
            sight = pair[1].get("visible_to")
            visible = isinstance(sight, dict) and sight.get(side) is True
            if continuous and runs and runs[-1]["end"] == t - 1 and runs[-1]["levels"] == levels:
                runs[-1]["end"] = t
                runs[-1]["seen_seconds"] += int(visible)
            else:
                runs.append({"start": t, "end": t, "levels": levels, "seen_seconds": int(visible)})
        result["samples"] += 1
        last_time, prior_ranks = t, ranks
    return result


def render_level_review(data, matchup):
    if not data:
        return ""
    names = [escape(matchup[who]["name"]) for who in ("self", "opponent")]
    parts = ['<h3 id="mid-levels">Кто был впереди по уровню до 10:00</h3>']
    if not data["samples"]:
        edges = [entry for entry in data["journal"] if entry[1] != entry[2]]
        if edges:
            parts.append("<p>Снимки опыта OpenDota. Время относится к замеру, точная секунда получения уровня неизвестна.</p><ul>")
            for t, own, other in edges:
                parts.append(f"<li>{_clock(t)} · {names[0]} — {own} ур., {names[1]} — {other} ур.</li>")
            parts.append("</ul>")
        else:
            parts.append("<p>По доступным снимкам преимущество в уровнях не установлено.</p>")
        return "".join(parts)
    own_runs = [w for w in data["runs"] if w["levels"][0] > w["levels"][1]]
    if own_runs:
        best = max(own_runs, key=lambda w: (w["levels"][0] - w["levels"][1], w["end"] - w["start"]))
        stamp = _clock(best["start"])
        if best["end"] != best["start"]:
            stamp += "–" + _clock(best["end"])
        parts.append(f'<p>На <strong>{stamp}</strong> твой {names[0]} был '
                     f'{best["levels"][0]}-го уровня, а {names[1]} — {best["levels"][1]}-го. '
                     'Проверь, во что ты успел вложить очко и мог ли подойти к сопернику.</p>')
    else:
        parts.append(f'<p>В {data["samples"]} парных кадров преимущество {names[0]} по уровню при двух живых мидерах не зафиксировано.</p>')
    if data["runs"]:
        parts.append('<p>Перевес в уровнях учитывается на всей карте. Для размена нужны ещё обзор, дистанция и готовая кнопка.</p>')
        parts.append('<details><summary>Все интервалы перевеса, пока оба героя живы</summary><ul>')
        for run in data["runs"]:
            a, b = run["levels"]
            stamp = _clock(run["start"]) if run["start"] == run["end"] else f'{_clock(run["start"])}–{_clock(run["end"])}'
            parts.append(f'<li>{stamp} · {names[0]} {a} / {names[1]} {b}. '
                         f'Соперник виден команде в {run["seen_seconds"]} кадрах из {run["end"] - run["start"] + 1}.</li>')
        parts.append('</ul></details>')
    if data["upgrades"]:
        parts.append('<details><summary>Когда в повторе появился новый ранг навыка</summary><ul>')
        for u in data["upgrades"]:
            parts.append(f'<li>{_clock(u["time"])} · {names[u["who"]]}, {u["level"]} ур. · '
                         f'{escape(u["skill"])} — ранг {u["rank"]}.</li>')
        parts.append('</ul></details>')
    return "".join(parts)
