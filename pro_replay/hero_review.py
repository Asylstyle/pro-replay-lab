"""Compare actual output with earlier performances on the same hero and position."""

from collections import Counter
from functools import lru_cache
import gzip
from html import escape
import json
from pathlib import Path
from statistics import median, quantiles

from .analysis import _int
from .impact import DIMENSIONS, SLOTS, _total, amount
from .patch_reference import client_cohort, patch_mismatch


COHORT_HISTORY = Path(__file__).resolve().parents[1] / "examples" / "patch-hero-reference.json.gz"


def extract_hero_samples(raw):
    """One compact observation per identifiable slot, including anonymous players."""
    players = []
    for p in raw.get("players") or []:
        if isinstance(p, dict) and _int(p.get("player_slot")) in SLOTS:
            players.append(p)
    slot_counts = Counter(_int(p["player_slot"]) for p in players)
    observations = []
    for p in players:
        slot = _int(p["player_slot"])
        if slot_counts[slot] != 1:
            continue
        position = _int(p.get("position_est"))
        observations.append({"slot": slot, "hero_id": _int(p.get("hero_id")),
                             "position": position if position in range(1, 6) else None,
                             "values": {key: _total(p, fields) for key, _, _, fields in DIMENSIONS}})
    for slots in (SLOTS[:5], SLOTS[5:]):
        side = [s for s in observations if s["slot"] in slots]
        totals = {}
        for key, *_ in DIMENSIONS:
            values = [s["values"][key] for s in side]
            totals[key] = sum(values) if len(values) == 5 and None not in values else None
        for sample in side:
            sample["team_totals"] = totals
    return observations


def _population(row):
    league = _int(row.get("league_id"))
    lobby = _int(row.get("lobby_type"))
    mode = _int(row.get("game_mode"))
    if mode is None:
        return None
    if league is not None and league > 0:
        return ("турнирные карты", mode)
    if league == 0 and lobby == 7:
        return ("рейтинговые карты", mode)
    if league == 0 and lobby is not None:
        return (f"карты лобби OpenDota #{lobby}", mode)
    return None


def _summary(values, current):
    valid = [v for v in values if v is not None]
    stats = {"n": len(valid), "median": median(valid) if valid else None, "middle_half": None,
             "lower": sum(v < current for v in valid) if current is not None else None,
             "equal": sum(v == current for v in valid) if current is not None else None}
    if len(valid) >= 4:
        quartiles = quantiles(valid, n=4, method="inclusive")
        stats["middle_half"] = [quartiles[0], quartiles[2]]
    return stats


def _measure(sample, duration, key):
    value = amount(sample["values"].get(key))
    total = amount(sample["team_totals"].get(key))
    per_min = 60 * value / duration if duration and value is not None else None
    share = value / total if total and value is not None else None
    return {"value": value, "total": total, "rate": per_min, "share": share}


def hero_benchmark(row, archive_rows):
    """A descriptive, strictly historical reference. Counts represent player-matches."""
    samples = row.get("hero_samples") or []
    slot = (row.get("detail") or {}).get("slot")
    if slot is None:
        slot = row.get("player_slot")
    if slot is None:
        current = [s for s in samples if s["hero_id"] == row.get("hero_id")]
    else:
        current = [s for s in samples if s["slot"] == _int(slot)]
    if len(current) != 1:
        return None
    target = current[0]
    patch_field = "patch_version" if row.get("patch_version") else "patch"
    patch = row.get(patch_field)
    population = _population(row)
    start = amount(row.get("start_time"))
    hero_history, other_history = [], []
    seen = set()
    if target["position"] and target["hero_id"] and start and patch is not None and population:
        for prior in archive_rows:
            mid = _int(prior.get("match_id"))
            time = amount(prior.get("start_time"))
            duration = amount(prior.get("duration"))
            if (not mid or mid == _int(row.get("match_id")) or not time or not duration or time + duration > start
                    or prior.get(patch_field) != patch or _population(prior) != population):
                continue
            for sample in prior.get("hero_samples") or []:
                if sample["position"] != target["position"] or not sample["hero_id"]:
                    continue
                identity = (mid, sample["slot"])
                if identity in seen:
                    continue
                seen.add(identity)
                group = hero_history if sample["hero_id"] == target["hero_id"] else other_history
                group.append((sample, duration, mid))
    metrics = {}
    for key, label, _, _ in DIMENSIONS:
        same = [_measure(s, duration, key) for s, duration, _ in hero_history]
        other = [_measure(s, duration, key) for s, duration, _ in other_history]
        measured = _measure(target, amount(row.get("duration")), key)
        metrics[key] = {
            "label": label, **measured,
            "hero_rate": _summary((m["rate"] for m in same), measured["rate"]),
            "hero_share": _summary((m["share"] for m in same), measured["share"]),
            "position_rate": _summary((m["rate"] for m in other), measured["rate"]),
        }
    return {"hero_id": target["hero_id"], "position": target["position"],
            "patch": patch, "version_known": patch_field == "patch_version",
            "population": population[0] if population else None,
            "same_n": len(hero_history), "other_n": len(other_history),
            "examples": sorted({mid for _, _, mid in hero_history}, reverse=True),
            "metrics": metrics}


def _number(value, percentage=False):
    if value is None:
        return "—"
    number = value * 100 if percentage else value
    rounded = f"{number:,.2f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")
    return rounded + ("%" if percentage else "")


def _overview(metrics):
    """Surface absolute tower output and one evidence-backed combat/support comparison."""
    facts = []
    towers = metrics["buildings"]
    if towers["rate"] is not None:
        text = f'Игрок нанёс {_number(towers["value"])} урона строениям'
        if towers["total"] is not None:
            text += f' при командном итоге {_number(towers["total"])}'
        text += f'. Темп — {_number(towers["rate"])} в минуту.'
        for key, label in (("hero_rate", "Тот же герой"), ("position_rate", "Другие герои той же позиции")):
            stats = towers[key]
            if stats["n"] == 1:
                text += f' {label} — одно наблюдение, {_number(stats["median"])} в минуту.'
            elif stats["n"] > 1:
                text += f' {label} — медиана {_number(stats["median"])} в минуту по {stats["n"]} наблюдениям.'
        facts.append(f'<div class="fact"><b>Давление на строения.</b> {text} <a href="#timeline">Тайминги башен</a></div>')
    standout = None
    for key in ("damage", "control", "healing", "fights", "vision"):
        m = metrics[key]
        if not m["rate"]:
            continue
        if m["hero_rate"]["n"] >= 4:
            peers, group = m["hero_rate"], "этого героя на той же позиции"
        else:
            peers, group = m["position_rate"], "других героев той же позиции"
        if peers["n"] < 4 or not peers["lower"]:
            continue
        if standout is None or peers["lower"] / peers["n"] > standout[0]:
            standout = peers["lower"] / peers["n"], key, group, peers
    if standout is not None:
        _, key, group, peers = standout
        m = metrics[key]
        link = '<a href="#timeline">Тайминги обзора</a>' if key == "vision" else '<a href="#fights">Разобрать драки</a>'
        facts.append(f'<div class="fact"><b>{escape(m["label"])}.</b> Игрок — {_number(m["rate"])} в минуту, '
                     f'медиана {group} — {_number(peers["median"])}. '
                     f'В {peers["lower"]} из {peers["n"]} предыдущих выступлений этой группы '
                     f'темп был ниже. {link}</div>')
    return ''.join(facts)


@lru_cache(maxsize=4)
def _saved_hero_rows(path, mtime, size):
    with path.open("rb") as stream:
        return json.loads(gzip.decompress(stream.read()))


def render_coach_hero_review(row, raw, model, archive_rows, *, history_path=None):
    """Use the active patch model's verified peer IDs for a match detail."""
    if raw is None:
        raw = {**row, "leagueid": row.get("league_id")}
    cohort = client_cohort(raw, model)
    if cohort is None:
        return render_hero_review(row, [], reason="Для сравнения нужен рейтинговый All Pick или подтверждённая профессиональная карта.")
    label = ("рейтинговые All Pick со средним MMR лобби 7300+"
             if cohort == "ranked_7300" else "профессиональные матчи")
    reason = patch_mismatch(model, raw)
    if reason:
        return render_hero_review(row, [], reason=reason)
    patch = model["policy"]["patch"]
    ids = set(model["cohort_ids"][cohort])
    sources = []
    for path in dict.fromkeys((history_path, COHORT_HISTORY)):
        if path is not None and Path(path).is_file():
            path = Path(path)
            stat = path.stat()
            sources.append(_saved_hero_rows(path, stat.st_mtime_ns, stat.st_size))
    sources.append(archive_rows)
    # IDs also identify the exact Valve release when an archived scorecard
    # only has OpenDota's broader numeric patch. Keep any explicit conflict out.
    rows = ({**p, "patch_version": patch} for source in sources for p in source
            if p.get("match_id") in ids and p.get("patch_version") in (None, patch))
    return render_hero_review({**row, "patch_version": patch}, rows, peer_label=label)


def render_hero_review(row, archive_rows, *, peer_label=None, reason=None):
    result = hero_benchmark(row, archive_rows)
    if result is None:
        return "<p>В сохранённой карте нет показателей героя по слотам. Обнови карточку матча для сравнения.</p>"
    role = result["position"]
    hero = escape(str(row.get("hero_name") or f'Герой #{result["hero_id"]}'))
    intro = [f"<p><b>{hero}</b>"]
    if role is not None:
        intro.append(f" · позиция {role} по оценке OpenDota")
    else:
        intro.append(" · оценка позиции OpenDota отсутствует")
    if result["population"]:
        intro.append(" · " + escape(result["population"]))
    if result["patch"] is not None:
        label = "версия" if result["version_known"] else "ID патча OpenDota"
        intro.append(f' · {label} {escape(str(result["patch"]))}')
    intro.append(".</p>")
    if peer_label:
        intro.append(f"<p>Группа сравнения — {escape(peer_label)}.</p>")
    if result["same_n"] or result["other_n"]:
        intro.append(f'<p>В более ранних матчах архива найдено выступлений на этой позиции '
                     f'тем же героем — <b>{result["same_n"]}</b>, другими героями — <b>{result["other_n"]}</b>. '
                     'Учтены обе стороны карты и тот же режим игры.</p>')
    elif reason:
        intro.append(f"<p>{escape(reason)}</p>")
    elif role is not None:
        intro.append("<p>В архиве нет подходящих более ранних наблюдений с известными позицией, патчем и типом матча.</p>")
    intro.append(_overview(result["metrics"]))
    headings = ["Показатель", "Игрок / команда", "Доля игрока", "Игрок за минуту"]
    if result["same_n"]:
        headings += ["Герой за минуту · медиана", "Доля героя · медиана"]
    if result["other_n"]:
        headings.append("Другие на позиции за минуту · медиана")
    table = ['<div class="scroll"><table><thead><tr>']
    table.extend(f'<th scope="col">{h}</th>' for h in headings)
    table.append('</tr></thead><tbody>')
    for metric in result["metrics"].values():
        cells = [escape(metric["label"]), f'{_number(metric["value"])} / {_number(metric["total"])}',
                 _number(metric["share"], True), _number(metric["rate"])]
        baselines = []
        if result["same_n"]:
            baselines += [("hero_rate", False), ("hero_share", True)]
        if result["other_n"]:
            baselines.append(("position_rate", False))
        for field, percentage in baselines:
            stats = metric[field]
            entry = f'{_number(stats["median"], percentage)} <small>(n={stats["n"]})</small>'
            if field == "hero_rate" and metric["rate"] is not None and stats["middle_half"]:
                lower, upper = stats["middle_half"]
                if metric["rate"] < lower:
                    entry += '<br><small>игрок ниже 25-го процентиля</small>'
                elif metric["rate"] > upper:
                    entry += '<br><small>игрок выше 75-го процентиля</small>'
            cells.append(entry)
        table.append('<tr>' + ''.join(f'<td>{cell}</td>' for cell in cells) + '</tr>')
    table.append('</tbody></table></div>')
    notes = []
    if result["same_n"]:
        # The spread of historical objective damage gives context to a single match's team share.
        tower = result["metrics"]["buildings"]["hero_rate"]
        if tower["middle_half"]:
            lo, hi = tower["middle_half"]
            notes.append(f'<p>Урон строениям в минуту у {hero} на позиции {role} '
                         f'в предыдущих играх — медиана {_number(tower["median"])}, '
                         f'середина распределения (25–75-й процентили) от {_number(lo)} до {_number(hi)}. '
                         f'Здесь — {_number(result["metrics"]["buildings"]["rate"])}.</p>')
        elif tower["n"]:
            notes.append(f'<p>Число наблюдений для урона строениям — {tower["n"]}. '
                         'Разброс по такой выборке оценить трудно.</p>')
        links = " · ".join(f'<a href="https://www.opendota.com/matches/{i}">#{i}</a>' for i in result["examples"])
        notes.append(f'<details><summary>Карты этого героя на той же позиции</summary><p>{links}</p></details>')
    notes.append('<p class="meta">Текущая доля показывает вклад в суммарный показатель команды. '
                 'Темп за минуту учитывает длительность карты. Пропуски исключаются отдельно для каждого показателя. '
                 'При нулевом командном итоге доля не определена.</p>')
    return "".join(intro + table + notes)
