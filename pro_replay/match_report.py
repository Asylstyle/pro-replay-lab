"""One self-contained match review. Every number links back to a match or a definition."""

from .analysis import CORE_ITEMS
from .build_review import render_build
from .fight_review import render_fights
from .hero_review import render_hero_review
from .impact import prior_maps, observations
from .report import _clock, _date, _h, _item


STYLE = """
:root{color-scheme:dark;--bg:#10171e;--panel:#17232c;--text:#f1f3ef;--soft:#bac6cc;--line:#34434c;--lime:#d4ec80}
*{box-sizing:border-box}body{margin:0;color:var(--text);background:var(--bg);font:16px/1.5 Inter,Segoe UI,sans-serif}
main,nav{max-width:1060px;margin:auto;padding:0 24px}header{border-top:4px solid var(--lime);padding:26px 0 12px}
h1,h2,p{margin:0 0 .55em}h1{font-size:clamp(36px,6vw,70px);line-height:1.05;letter-spacing:-.04em}
h2{font-size:clamp(23px,3vw,32px);line-height:1.16;letter-spacing:-.025em}h3{margin:18px 0 6px}
section{margin:30px 0;scroll-margin-top:12px}.meta,small,caption{color:var(--soft)}.meta{font-size:.91rem}
.topline{color:var(--lime);font:600 .82rem/1.8 Consolas,monospace;letter-spacing:.08em}
.cards{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);background:var(--line);gap:1px}
.card{background:var(--panel);padding:13px 16px}.card b{display:block;font-size:clamp(22px,3vw,34px);font-variant-numeric:tabular-nums}
a,time,.selected{color:var(--lime)}a:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--lime)}
nav{padding:12px 0;border-bottom:1px solid var(--line);display:flex;gap:8px 18px;flex-wrap:wrap}a{text-underline-offset:.2em}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:.94rem}
td,th{text-align:right;padding:7px 9px;border-bottom:1px solid var(--line);white-space:nowrap}
thead th{white-space:normal;vertical-align:bottom}section{min-width:0}
th{color:var(--soft);font-size:.8rem}td:first-child,th:first-child{text-align:left;white-space:normal}
.fact{padding:9px 0;border-bottom:1px solid var(--line)}.fact p{margin:0}.fact small{display:block}
.bar{display:block;height:6px;background:#30454d;border-radius:4px;min-width:65px}.bar i{display:block;background:var(--lime);height:100%;border-radius:4px}
details{margin:14px 0}summary{cursor:pointer;color:var(--lime)}summary+p{margin:10px 0}
select{padding:6px 10px;background:var(--panel);color:var(--text);border:1px solid var(--line);font:inherit}
.events{list-style:none;padding:0}.events li{display:flex;gap:17px;border-bottom:1px solid var(--line);padding:5px 0}
.events li[hidden]{display:none}time{font: .94rem/1.55 Consolas,monospace;min-width:48px}footer{border-top:1px solid var(--line);padding:18px 0 42px}
@media(max-width:660px){main{padding:0 15px}.cards{grid-template-columns:1fr 1fr}.card{padding:10px}td,th{padding:6px}.meta{font-size:.85rem}}
"""


def num(value):
    if value is None:
        return "—"
    return f"{value:,.1f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")


def pct(value):
    return "—" if value is None else num(value * 100) + " %"


def table(headers, rows):
    heading = "".join(f'<th scope="col">{_h(label)}</th>' for label in headers)
    body = "".join("<tr>" + "".join(f"<td>{_h(cell)}</td>" for cell in cells) + "</tr>" for cells in rows)
    return f'<div class="scroll"><table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>'


def impact_section(detail):
    data = detail.get("impact")
    if not data:
        return "<p>Для расчёта нужны данные пяти игроков одной команды.</p>"
    rows = [(m["label"], num(m["value"]), num(m["total"]), pct(m["share"]), num(m["weight"]))
            for m in data["metrics"]]
    breakdown = table(("Показатель", "Игрок", "Команда", "Доля игрока", "Вес"), rows)
    ranking = []
    roster = sorted(data["roster"], key=lambda p: -(p["score"] or 0))
    for player in roster:
        score = player["score"]
        highlight = ' class="selected"' if player["slot"] == detail["slot"] else ""
        bar = f'<span class="bar"><i style="width:{score:.2%}"></i></span>' if score is not None else ""
        ranking.append(f'<tr{highlight}><td>{_h(player["name"])}</td><td>{_h(player["hero"])}</td>'
                       f'<td>{num(player["kills"])} / {num(player["deaths"])} / {num(player["assists"])}</td>'
                       f'<td>{num(player["net_worth"])}</td><td>{pct(score)}{bar}</td></tr>')
    return ('<p>Индекс распределяет 100 % между пятью союзниками по записанным действиям. '
            'Веса заданы вручную. Причинный вклад в победу эта формула не измеряет.</p>'
            '<div class="scroll"><table><thead><tr><th>Игрок</th><th>Герой</th><th>K / D / A</th>'
            '<th>Ценность</th><th>Индекс</th></tr></thead><tbody>' + "".join(ranking) + '</tbody></table></div>'
            '<details open><summary>Как посчитан процент</summary><p>Для каждого показателя делим значение '
            'игрока на сумму пяти союзников. Умножаем долю на вес, складываем и делим на сумму использованных весов. '
            'Показатель пропускается при нулевой сумме или пропуске у любого союзника. Для индекса нужны минимум три показателя.</p>'
            + breakdown + f'<small>Доступно {data["available"]} из 6 показателей. Сумма использованных весов — '
            f'{data["weight_sum"]} из 100. Один эпизод может дать убийство и несколько ассистов разным игрокам.</small></details>')


def standout_section(row, prior):
    patterns, timings = observations(row, prior)
    notes = []
    for p in patterns:
        comparison = (f'Ранее встречалось в {p["previous"]} из {p["comparable"]} сопоставимых карт.'
                      if p["comparable"] else "Для сравнения пока нет более ранних карт с нужными журналами.")
        peers = (row.get("detail") or {}).get("ally_sequences", {}).get(p["label"])
        if peers and peers["known"]:
            team_note = ("Единственный из пяти игроков команды с таким эпизодом." if peers == {"found": 0, "known": 4}
                         else f'Среди остальных союзников — {peers["found"]} из {peers["known"]} с доступными данными.')
            comparison = team_note + " " + comparison
        notes.append(f'<div class="fact"><p><b>{_h(p["label"])}</b> · {_clock(p["before_time"])} → '
                     f'{_clock(p["after_time"])} (интервал {_clock(p["gap"])})</p><small>{comparison}</small></div>')
    impact = (row.get("detail") or {}).get("impact") or {}
    leaders = []
    for metric in impact.get("metrics", []):
        if metric["share"] == 1:
            notes.append(f'<div class="fact"><b>{_h(metric["label"])}</b> · весь командный объём, '
                         f'{num(metric["value"])} из {num(metric["total"])}.</div>')
        elif metric.get("sole_leader") or (metric["share"] is not None and metric["share"] > 0.5):
            leaders.append(metric)
    if leaders:
        m = max(leaders, key=lambda metric: metric["share"])
        notes.append(f'<div class="fact"><b>{_h(m["label"])}</b> · первое место среди пяти союзников. '
                     f'{num(m["value"])} из {num(m["total"])} ({pct(m["share"])}).</div>')
    if timings:
        notes.append('<h3>Когда появились предметы</h3>' + table(
            ("Предмет", "На этой карте", "Медиана ранее", "Отклонение", "Карт с предметом"),
            [(_item(t["key"]), _clock(t["time"]), _clock(t["median"]),
              ("раньше на " if t["time"] < t["median"] else "позже на ") + _clock(abs(t["time"] - t["median"]))
              if t["time"] != t["median"] else "совпадает", t["count"]) for t in timings]))
    criteria = "версия Valve по календарю" if row.get("patch_version") else "ID патча OpenDota"
    notes.append(f'<p class="meta">База сравнения — более ранние карты этого игрока на том же герое и патче '
                 f'({criteria}). В локальном архиве их {len(prior)}. Для каждой покупки учитываются только полные '
                 'журналы с этим предметом. Частота эпизода считается по картам, где его наличие или отсутствие известно.</p>')
    if not patterns and not timings and not leaders and not any(m["share"] == 1 for m in impact.get("metrics", [])):
        notes.insert(0, '<p>В доступных данных нет выделенных эпизодов или сопоставимых таймингов покупок.</p>')
    return "".join(notes)


def timeline_section(row):
    filters = {"key": "Ключевые события", "all": "Все события", "combat": "Убийства, смерти и выкупы",
               "object": "Башни, Roshan и Aegis", "item": "Основные предметы", "vision": "Варды",
               "shop": "Остальные покупки"}
    kinds = {"kill": "combat", "death": "combat", "buyback": "combat", "firstblood": "combat",
             "tower": "object", "roshan": "object", "aegis": "object", "observer": "vision", "sentry": "vision"}
    events = [(e["time"], kinds.get(e["kind"], e["kind"]), e["detail"]) for e in row.get("timeline", [])]
    events.extend((e["time"], "item" if e["key"] in CORE_ITEMS else "shop", "Купил " + _item(e["key"]))
                  for e in row.get("purchases", []))
    options = "".join(f'<option value="{key}">{label}</option>' for key, label in filters.items())
    entries = "".join(f'<li data-kind="{_h(kind)}"><time>{_clock(t)}</time><span>{_h(label)}</span></li>'
                      for t, kind, label in sorted(events, key=lambda event: event[0]))
    coverage = {"покупки": row.get("purchase_log_available")}
    labels = {"kill": "убийства", "death": "смерти", "buyback": "выкупы", "tower": "башни",
              "observer": "Observer Ward", "sentry": "Sentry Ward"}
    coverage.update({label: row.get("event_coverage", {}).get(kind) for kind, label in labels.items()})
    missing = [name for name, available in coverage.items() if not available]
    note = "Неполные или отсутствующие журналы — " + ", ".join(missing) + "." if missing else "Основные журналы доступны."
    return (f'<label>Показать <select id="filter">{options}</select></label><span id="count" class="meta" '
            f'aria-live="polite"></span><p class="meta">{note} Отсчёт от игрового 0:00.</p>'
            f'<ul class="events" id="events">{entries}</ul>')


def render_match(row, archive, references=None):
    detail = row.get("detail") or {}
    hero = row.get("hero_name") or f'Герой #{row.get("hero_id")}'
    player = row.get("player_name") or ("Игрок " + str(row.get("side") or "").title()).strip()
    mid = int(row["match_id"])
    game_label = "Рейтинговый матч" if row.get("lobby_type") == 7 and not row.get("league_id") else None
    subtitle = " · ".join(str(v) for v in (row.get("team_name"), row.get("opponent"), row.get("league_name") or game_label,
                           _date(row.get("start_time")), row.get("patch_version") or
                           (f'ID патча {row["patch"]}' if row.get("patch") else None)) if v)
    outcome = {True: "Победа", False: "Поражение"}.get(row.get("win"), "Итог неизвестен")
    review = detail.get("fight_review") or {}
    cards = [("K / D / A", " / ".join(num(row.get(k)) for k in ("kills", "deaths", "assists"))),
             ("Участие в убийствах", pct(row.get("kill_participation"))),
             ("Драки в журнале", num(review.get("recorded")) if review.get("available") else "—"),
             (outcome, _clock(row.get("duration")))]
    metrics = "".join(f'<div class="card"><small>{label}</small><b>{value}</b></div>' for label, value in cards)
    checkpoints = detail.get("checkpoints", [])
    economy = table(("Минута", "Добивания", "Получено золота", "Опыт", "Ценность", "Урон героям", "Урон строениям"),
                    [(_clock(p["time"]), *(num(p[k]) for k in ("lh", "gold", "xp", "worth", "damage", "buildings")))
                     for p in checkpoints]) if checkpoints else "<p>Нет десятиминутных снимков экономики.</p>"
    fights = detail.get("teamfights", [])
    combat = table(("Интервал", "Убийства", "Смерти", "Урон", "Лечение", "Изменение золота"),
                   [(f'{_clock(f["start"])} — {_clock(f["end"])}', *(num(f[k]) for k in
                     ("kills", "deaths", "damage", "healing", "gold"))) for f in fights]) if fights else (
                     "<p>Нет зарегистрированных интервалов с показателями игрока.</p>" if detail.get("fights_available")
                     else "<p>Журнал командных драк отсутствует.</p>")
    if "fight_review" in detail:
        combat = render_fights(review)
    blocks = [("hero", "Герой и позиция", render_hero_review(row, references if references is not None else archive)),
              ("fights", "Что сделал в драках", combat),
              ("build", "Предметы против драфта", render_build(detail.get("build"), patch_version=row.get("patch_version"))),
              ("standout", "Чем выделился игрок", standout_section(row, prior_maps(row, archive))),
              ("impact", "Доли внутри команды", impact_section(detail)),
              ("economy", "Экономика по ходу карты", economy),
              ("timeline", "Таймлайн действий", timeline_section(row))]
    nav = " ".join(f'<a href="#{key}">{label}</a>' for key, label, _ in blocks)
    sections = "".join(f'<section id="{key}"><h2>{label}</h2>{content}</section>' for key, label, content in blocks)
    links = f'<a href="https://www.opendota.com/matches/{mid}">Журналы OpenDota</a> · <a href="https://www.dotabuff.com/matches/{mid}">Карта на Dotabuff</a>'
    if row.get("patch_version"):
        links += ' · <a href="https://www.dota2.com/patches">Календарь патчей Valve</a>'
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width, initial-scale=1"><title>{_h(player)} · {_h(hero)} · {mid}</title>'
            f'<style>{STYLE}</style></head><body><main><header><p class="topline">PRO REPLAY LAB / МАТЧ {mid}</p>'
            f'<h1>{_h(player)} · {_h(hero)}</h1><p class="meta">{_h(subtitle)}</p></header><div class="cards">{metrics}</div>'
            f'<nav aria-label="Разделы карты">{nav}</nav>{sections}<footer>{links}</footer></main><script>'
            'const select=document.getElementById("filter");function filterEvents(){let count=0;'
            'for(const el of document.querySelectorAll("#events li")){const k=el.dataset.kind;'
            'el.hidden=!(select.value==="all"||select.value===k||(select.value==="key"&&k!=="shop"&&k!=="vision"));'
            'if(!el.hidden)count++}document.getElementById("count").textContent=" · Событий: "+count;}'
            'select.addEventListener("change",filterEvents);filterEvents();</script></body></html>')
