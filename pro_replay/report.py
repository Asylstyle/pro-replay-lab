"""Standalone, Russian-language player report. No web assets or runtime dependencies."""

from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit

from .analysis import first_pair

_STYLE = """
:root{color-scheme:dark;--bg:#101820;--panel:#19252f;--line:#354550;--text:#f1f2ec;--soft:#b9c5c9;--accent:#d4ec80;--mono:ui-monospace,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 Inter,"Segoe UI",sans-serif}
main{max-width:1060px;margin:0 auto;padding:36px 28px 60px}header{border-top:3px solid var(--accent);padding:24px 0 10px}
.eyebrow,.tag{color:var(--accent);font:600 .76rem var(--mono);letter-spacing:.1em;text-transform:uppercase}
h1{font-size:clamp(2.4rem,7vw,4.6rem);line-height:1.03;margin:15px 0 18px;letter-spacing:-.045em;overflow-wrap:anywhere}
h2{font-size:1.65rem;line-height:1.2;letter-spacing:-.028em;margin:34px 0 12px}p{margin:8px 0}
.muted,.sub,small{color:var(--soft)}.sub,small{font-size:.88rem}.muted{max-width:78ch}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1px;border:1px solid var(--line);background:var(--line);margin:18px 0 0}
.metric{padding:16px 18px;background:var(--panel)}.metric b{display:block;font:600 1.75rem var(--mono);margin:6px 0}
.note{margin:13px 0;padding:4px 0 4px 14px;border-left:3px solid var(--accent);max-width:90ch}
.fact{padding:12px 0;border-bottom:1px solid var(--line)}.fact p{margin:3px 0}
a{color:var(--accent);text-underline-offset:3px}a:hover{color:white}a:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:4px}
details{border-top:1px solid var(--line);scroll-margin-top:12px}details:last-of-type{border-bottom:1px solid var(--line)}
summary{cursor:pointer;list-style:none;padding:15px 4px;display:grid;grid-template-columns:20px minmax(0,1fr) auto;align-items:baseline;gap:8px}
summary::-webkit-details-marker{display:none}summary:before{content:'+';font:1.2rem var(--mono);color:var(--accent)}details[open] summary:before{content:'−'}
summary strong{overflow-wrap:anywhere}.badge{font:.82rem var(--mono);color:var(--soft)}.body{padding:0 14px 17px 30px}.body ul{padding-left:0;list-style:none;margin:12px 0}
.body li{display:grid;grid-template-columns:62px minmax(0,1fr);gap:12px;padding:5px 0;border-bottom:1px solid #2b3944}
#match-filter{font:inherit;color:var(--text);background:var(--panel);border:1px solid var(--line);padding:8px 10px;width:min(100%,340px);margin:5px 0 12px}
time{font: .94rem/1.5 var(--mono);color:var(--accent)}.meta{color:var(--soft);font-size:.92rem}
@media(max-width:560px){main{padding:19px 15px 40px}.body{padding-left:8px}summary{gap:4px}.metrics{grid-template-columns:1fr 1fr}.metric{padding:12px}}
@media print{body{background:#fff;color:#111}main{max-width:none}.metrics,.metric{background:#fff}.muted,.sub,small,.meta,.badge{color:#333}.tag,.eyebrow,time,a{color:#293f0a}.body{break-inside:avoid}}
"""


def _h(value):
    return escape(str(value), quote=True)


def _clock(seconds):
    if seconds is None:
        return "—"
    whole = int(round(seconds))
    minutes, rest = divmod(abs(whole), 60)
    return f"{'−' if whole < 0 else ''}{minutes}:{rest:02}"


def _date(timestamp):
    if timestamp is None:
        return ""
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%d.%m.%Y")
    except (OSError, TypeError, ValueError, OverflowError):
        return ""


def _item(key):
    common = {"bfury": "Battle Fury", "ultimate_scepter": "Aghanim’s Scepter",
              "manta": "Manta Style", "sheepstick": "Scythe of Vyse",
              "sphere": "Linken’s Sphere", "blink": "Blink Dagger",
              "greater_crit": "Daedalus", "lesser_crit": "Crystalys", "gungir": "Gleipnir"}
    return common.get(key, key.replace("_", " ").title())


def _metric(label, value, caption):
    return f'<div class="metric"><small>{_h(label)}</small><b>{_h(value)}</b><small>{_h(caption)}</small></div>'


def _evidence(ids, anchors):
    links = [f'<a href="#{anchors[i]}">#{_h(i)}</a>' for i in ids if i in anchors]
    return ' <span class="sub">Матчи: ' + " · ".join(links[:4]) + '</span>' if links else ''


def _patterns(rows, stats, anchors):
    facts = []
    if stats.get("case_note"):
        case_ids = [r.get("match_id") for r in rows if r.get("sample_partial")]
        facts.append(f'<div class="note"><p>{_h(stats["case_note"])}</p>'
                     f'{_evidence(case_ids, anchors)}</div>')

    coverage = stats.get("coverage", {})
    items = stats.get("first_core_items", [])
    if items:
        top = items[0]
        key = top["item"]
        ids = [r.get("match_id") for r in rows if r.get("first_core_item") == key]
        facts.append(f'<div class="fact"><p><b>{_h(_item(key))}</b> — первый предмет из '
                     f'отслеживаемого списка в {top["matches"]} из {coverage.get("first_core", len(ids))} '
                     f'матчей с зарегистрированным предметом. Медиана покупки — '
                     f'{_clock(top.get("median_time"))}.</p>{_evidence(ids, anchors)}</div>')
    pairs = stats.get("first_two_core_items", [])
    if pairs and pairs[0]["matches"] > 1:
        top = pairs[0]
        first, second = top["items"]
        ids = [r.get("match_id") for r in rows if first_pair(r) == (first, second)]
        facts.append(f'<div class="fact"><p><b>{_h(_item(first))} → {_h(_item(second))}</b> '
                     f'— первые две зарегистрированные покупки из списка в {top["matches"]} '
                     f'из {coverage.get("first_two_core", len(ids))} матчей с двумя покупками.</p>'
                     f'{_evidence(ids, anchors)}</div>')

    labels = {"tower": "Последний удар по башне", "kill": "Убийство героя", "observer": "Установка Observer Ward",
              "sentry": "Установка Sentry Ward"}
    for kind, label in labels.items():
        event = stats.get("events", {}).get(kind, {})
        total = event.get("available_matches", 0)
        if not total:
            continue
        count = event.get("before_or_at_ten_matches", 0)
        ids = event.get("before_or_at_ten_ids", [])
        facts.append(f'<div class="fact"><p><b>{label} к 10:00 включительно</b> — '
                     f'{count} из {total} матчей с доступным журналом.</p>{_evidence(ids, anchors)}</div>')
    return "".join(facts)


def _match(row, anchor, demo):
    match_id = row.get("match_id")
    hero = row.get("hero_name") or (f'Герой #{row["hero_id"]}' if row.get("hero_id") else "Герой неизвестен")
    date = _date(row.get("start_time")) or row.get("date")
    result = {True: "Победа", False: "Поражение"}.get(row.get("win"), "")
    title = " · ".join(str(x) for x in [hero, row.get("league_name"), date] if x)
    initial = " open" if row.get("sample_partial") and anchor == "m-0" else ""
    body = [f'<details id="{anchor}"{initial}><summary><strong>{_h(title)}</strong>'
            f'<span class="badge">#{_h(match_id)}</span></summary><div class="body">']
    teams = " — ".join(str(x) for x in (row.get("team_name"), row.get("opponent")) if x)
    meta = [teams, result, {"radiant": "Radiant", "dire": "Dire"}.get(row.get("side"))]
    if row.get("duration") is not None:
        meta.append("длительность " + _clock(row["duration"]))
    if row.get("patch") is not None:
        meta.append("ID патча " + str(row["patch"]))
    if all(row.get(k) is not None for k in ("kills", "deaths", "assists")):
        meta.append("K/D/A " + "/".join(str(row[k]) for k in ("kills", "deaths", "assists")))
    if row.get("last_hits_10") is not None:
        meta.append(f'{row["last_hits_10"]} добиваний к 10:00')
    if meta := [x for x in meta if x]:
        body.append('<p class="meta">' + _h(" · ".join(meta)) + '</p>')
    impact = (row.get("detail") or {}).get("impact") or {}
    if impact.get("score") is not None:
        body.append(f'<p><b>{impact["score"]:.1%}</b> — индекс вклада в команде '
                    f'по {impact["available"]} из 6 показателей.</p>')
    if row.get("_match_page"):
        body.append(f'<p><a href="{_h(row["_match_page"])}">Открыть полный разбор карты →</a></p>')
    timeline = [(e["time"], e["detail"]) for e in row.get("timeline", [])
                if isinstance(e.get("time"), (int, float)) and e.get("detail")]
    timeline.extend((e["time"], "Купил " + _item(e["key"])) for e in row.get("core_items", []))
    timeline.sort(key=lambda e: e[0])
    if timeline:
        body.append('<ul>' + ''.join(f'<li><time>{_clock(t)}</time><span>{_h(d)}</span></li>'
                                    for t, d in timeline) + '</ul>')
    if row.get("sample_partial"):
        body.append('<p class="sub">В таймлайне только события, перечисленные в источнике. '
                    'Момент получения предмета может отличаться от момента его покупки.</p>')
    else:
        availability = {"покупки": row.get("purchase_log_available")}
        events = row.get("event_coverage", {})
        for key, name in (("tower", "башни"), ("kill", "убийства"), ("observer", "Observer Ward"), ("sentry", "Sentry Ward")):
            availability[name] = events.get(key)
        present = [name for name, yes in availability.items() if yes]
        absent = [name for name, yes in availability.items() if not yes]
        if present:
            body.append('<p class="sub">Доступные журналы: ' + ", ".join(present) + '.</p>')
        if absent:
            body.append('<p class="sub">Данные отсутствуют или неполны: ' + ", ".join(absent) + '.</p>')
        if not timeline and present:
            body.append('<p class="sub">В доступных журналах нет действий из отслеживаемого списка.</p>')

    sources = []
    if not demo and str(match_id).isascii() and str(match_id).isdigit():
        sources.append(f'<a href="https://www.opendota.com/matches/{match_id}">Матч в OpenDota ↗</a>')
    url = row.get("source_url") or ""
    if not demo and isinstance(url, str) and url.startswith("https://"):
        try:
            if urlsplit(url).hostname:
                sources.append(f'<a href="{_h(url)}">Источник таймлайна ↗</a>')
        except ValueError:
            pass
    if sources:
        body.append('<p class="sub">' + ' · '.join(sources) + '</p>')
    return "".join(body) + '</div></details>'


def render_report(player: str, rows: list[dict], stats: dict, *, demo: bool = False) -> str:
    """Render an offline HTML file for a single player and an explicit match sample."""
    rows = sorted(rows, key=lambda r: r.get("start_time") or 0, reverse=True)
    anchors = {r.get("match_id"): f"m-{i}" for i, r in enumerate(rows)}
    coverage = stats.get("coverage", {})
    partial = sum(bool(r.get("sample_partial")) for r in rows)
    metrics = [_metric("Матчи", len(rows), "в текущей выборке")]
    outcomes = coverage.get("outcomes", 0)
    if outcomes:
        metrics.append(_metric("Победы", f'{stats.get("wins", 0)}/{outcomes}', "с известным результатом"))
    for key, nkey, label in [("median_last_hits_10", "last_hits_10", "Добивания к 10:00"),
                             ("median_kill_participation", "kill_participation", "Участие в убийствах")]:
        val = stats.get(key)
        if val is not None:
            value = f"{val:.0%}" if key == "median_kill_participation" else f"{val:g}"
            metrics.append(_metric(label, value, f'медиана · матчей: {coverage.get(nkey, 0)}'))
    if partial:
        metrics.append(_metric("Выборочные таймлайны", partial, "матчей с выписками из источника"))
    else:
        metrics.append(_metric("Журнал покупок", f'{coverage.get("purchase_logs", 0)}/{len(rows)}', "матчей с данными"))
    intro = "Синтетический пример для проверки отчёта." if demo else "Действия и тайминги в выбранных профессиональных матчах."
    patterns = _patterns(rows, stats, anchors)
    history = "".join(_match(r, anchors[r.get("match_id")], demo) for r in rows)
    search = ('<label for="match-filter">Найти карту по герою, турниру или ID</label><br>'
              '<input id="match-filter" type="search" autocomplete="off"> '
              '<span id="match-count" class="sub" aria-live="polite"></span>') if len(rows) > 1 else ''
    detail_note = ("Время отсчитывается от игрового 0:00. Знак минус обозначает действие до сигнала горна. "
                   "Открой матч, чтобы посмотреть таймлайн и покрытие.")
    return (f'<!doctype html><html lang="ru"><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{_h(player)} · Разбор матчей Dota 2</title><style>{_STYLE}</style>'
            f'<main><header><div class="eyebrow">Dota 2 / Анализ игрока</div><h1>{_h(player)}</h1>'
            f'<p class="muted">{intro}</p></header><div class="metrics">{"".join(metrics)}</div>'
            + (f'<section><h2>Наблюдения</h2>{patterns}</section>' if patterns else '')
            + f'<section><h2>Матчи и действия</h2><p class="muted">{detail_note}</p>'
            + search + (history or '<p>В выбранной выборке нет матчей.</p>') +
            '</section></main><script>'
            'function openMatch(){const id=location.hash.slice(1);const node=document.getElementById(id);'
            'if(node&&node.tagName==="DETAILS"){node.open=true;node.scrollIntoView();}}'
            'window.addEventListener("hashchange",openMatch);openMatch();'
            'const filter=document.getElementById("match-filter");if(filter){const update=()=>{let n=0;'
            'const query=filter.value.trim().toLocaleLowerCase();for(const d of document.querySelectorAll("details")){'
            'd.hidden=!d.querySelector("summary").textContent.toLocaleLowerCase().includes(query);if(!d.hidden)n++;}'
            'document.getElementById("match-count").textContent="Карт: "+n;};filter.addEventListener("input",update);update();}'
            '</script></html>')
