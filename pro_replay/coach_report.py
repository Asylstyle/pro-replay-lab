"""Standalone client lesson and the shared shell for the local coaching app."""

from html import escape
from statistics import median
from urllib.parse import urlsplit

from .coaching import FOCUSES
from .report import _clock, _date


STYLE = """
:root{color-scheme:dark;--bg:#101820;--panel:#18252d;--ink:#f4f4ec;--soft:#b6c6cd;--line:#384b55;--accent:#d5ed83;--aqua:#93d5dd}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.56 Inter,"Segoe UI",sans-serif}
main{max-width:1000px;margin:auto;padding:0 25px 56px}header,.masthead{border-top:4px solid var(--accent);padding:32px 0 16px}
h1,h2,h3,p{margin:0 0 .55em}h1,h2,h3{line-height:1.1;letter-spacing:-.033em}h1{font-size:clamp(36px,6vw,66px)}h2{font-size:clamp(24px,4vw,33px)}h3{font-size:1.25rem}
section{margin:32px 0}p{max-width:78ch}.muted,.meta,small,caption{color:var(--soft)}.eyebrow{color:var(--accent);font:600 .82rem/1.8 Consolas,monospace;letter-spacing:.07em}
a,summary{color:var(--accent);text-underline-offset:.21em}a:focus-visible,button:focus-visible,select:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--aqua);outline-offset:3px}
.focus,.exercise,.panel,.hero-card{background:var(--panel);padding:20px 23px;border:1px solid var(--line);border-radius:5px}
.focus{border-left:4px solid var(--accent)}.focus h2{font-size:clamp(24px,4vw,37px)}.focus p:last-child,.panel p:last-child{margin-bottom:0}
.stamp{color:var(--aqua);font:600 .94em Consolas,monospace}.episode{padding:17px 0;border-bottom:1px solid var(--line)}
.episode h3{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}.exercise{background:#1b2d2d;border-left:4px solid var(--aqua)}
.resource{padding:10px 0;border-top:1px solid var(--line)}.resource small{display:block;margin-top:.15em}
.resources{margin-top:12px}.resources summary{cursor:pointer}.resources[open] summary{margin-bottom:8px}
.battle{border-bottom:1px solid var(--line);padding:11px 0}.battle>summary{cursor:pointer;font-weight:650}
.battle ul{padding-left:1.35em}.item-option{padding:12px 0;border-top:1px solid var(--line)}
.item-option p{margin:.2em 0}.item-option small{display:block}.battle-body{padding:12px 0}
.decision-list{display:grid;gap:18px;padding:0;list-style:none}.decision-card{padding:20px 22px;
border:1px solid var(--line);border-radius:5px;background:var(--panel);overflow-wrap:anywhere}
.decision-title{display:flex;align-items:baseline;gap:.75rem}.decision-title h3{margin-top:0}
.rank{color:var(--accent);font:600 1.05em Consolas,monospace}.decision-tools{display:flex;flex-wrap:wrap;
gap:10px 32px;border-top:1px solid var(--line);padding-top:12px;margin:14px 0 12px}
.decision-tools dt{color:var(--soft);font-size:.86rem}.decision-tools dd{margin:0;font-weight:650}
.decision-review{margin-top:12px}.decision-review summary{cursor:pointer}.decision-review p{margin:.55em 0}
.decision-links{margin:.8em 0 0}.battle-body:target{outline:1px solid var(--aqua);outline-offset:5px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}td,th{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}td:first-child,th:first-child{text-align:left}th{color:var(--soft);font-size:.86rem}.scroll{overflow:auto}
nav,footer,.actions{display:flex;gap:10px 22px;flex-wrap:wrap;align-items:center}footer{border-top:1px solid var(--line);padding-top:15px}
button,.button{display:inline-block;padding:.65em 1em;background:var(--accent);color:#142118;border:1px solid var(--accent);border-radius:4px;font:600 1rem/1.4 inherit;cursor:pointer;text-decoration:none}
input,select{display:block;font:inherit;border:1px solid var(--line);border-radius:4px;padding:.65em .8em;background:var(--bg);color:var(--ink);max-width:100%}label{font-weight:600}.grid,.roster{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
@media(max-width:640px){main{padding:0 16px 40px}.focus,.exercise,.panel{padding:15px}section{margin:24px 0}.episode h3{gap:4px 10px}}
@media print{body{background:white;color:black}.focus,.panel,.exercise{background:white}.meta,.muted,small,th{color:#333}nav,footer{display:none}.resources{display:block}.episode{break-inside:avoid}a,.stamp,.eyebrow,summary{color:#294e55}}
"""


def h(value):
    return escape(str(value), quote=True)


def page(title, body):
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{h(title)} · Pro Replay Lab</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>')


def number(value):
    return "—" if value is None else f"{value:,.0f}".replace(",", " ")


def table(headings, rows):
    head = "".join(f'<th scope="col">{h(t)}</th>' for t in headings)
    content = "".join('<tr>' + ''.join(f'<td>{h(v)}</td>' for v in row) + '</tr>' for row in rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{content}</tbody></table></div>'


def pro_section(data):
    metrics = data.get("metrics", [])
    if not metrics:
        return f'<p>{h(data.get("reason", "Для ориентира недостаточно данных на 10:00."))}</p>'
    bands = all(m.get("interval") for m in metrics)
    headings = ("К 20:00", "Факт", "Ориентир модели", "Типичная ошибка¹")
    rows = [(m["label"], number(m["actual"]), number(m["expected"]), number(m["mae"])) for m in metrics]
    if bands:
        headings += ("Диапазон ориентира²",)
        rows = [r + ("–".join(number(v) for v in m["interval"]),) for r, m in zip(rows, metrics)]
    details = table(headings, rows)
    origin = {"train": "Эта карта входила в обучение.", "holdout": "Эта карта входила в отложенную проверку.",
              "development": "Эта карта использовалась при выборе модели для рейтинговых игр.",
              "external": "Эта карта находится за пределами обучающей и проверочной выборок."}.get(data.get("cohort"), "")
    seen = data["hero_train_n"]
    hero_note = (f'Выступлений на этом герое в обучении — {seen}.' if seen else
                 'Герой впервые встретился модели. Ориентир построен по экономике и месту в команде.')
    if seen and seen < 20:
        hero_note += ' Примеров мало: сравнение на этом герое менее надёжно.'
    features = ('темп фарма к 5-й и 10-й минутам, героя и экономику обеих команд' if data.get("feature_version") == 2
                else 'героя, порядок по золоту и экономику на 10:00')
    missing = (' Часть ранних наблюдений отсутствует; использованы типичные значения из обучения.'
               if data.get("missing_feature_count") else '')
    population = 'на независимой выборке рейтинговых матчей' if data.get("error_population") == "ranked" else 'на более поздних турнирных матчах'
    interval_note = ('<p class="meta">² Ширина диапазона рассчитана по ошибкам проверочной истории, использованной при выборе модели. '
                     'Она показывает неопределённость; попадание конкретного результата в диапазон не гарантировано.</p>' if bands else '')
    return (f'<p>По золоту на 10:00 игрок занимал место {h(data["farm_rank_10"])} из пяти в своей команде. '
            f'Модель учитывает {features}. {hero_note} {origin}{missing}</p>{details}'
            f'<p class="meta">¹ Средняя абсолютная ошибка {population}, в единицах строки. '
            'Турнирный ориентир помогает выбрать эпизод. Для объяснения разрыва нужно проверить маршрут, '
            f'распределение фарма и действия соперника.</p>{interval_note}')


def patch_benchmarks(data):
    if not data.get("metrics"):
        return '<p>' + h(data.get("reason", "Ориентир недоступен.")) + '</p>'
    counts = data["counts"]
    group = (f'позиции {data["position"]}' if data.get("position") in range(1, 6)
             else f'{data["rank"]}-му месту по золоту среди союзников на 10:00')
    intro = (f'<p>Патч {h(data["patch"])}. В выборке {counts["professional"]} про-матчей и '
             f'{counts["ranked_7300"]} рейтинговых игр со средним MMR от 7 300 по OpenDota. '
             f'В таблице показаны средние значения по {h(group)}.</p>')
    if data["in_reference"]:
        intro += '<p class="meta">Эта карта входит в итоговую выборку ориентиров.</p>'
    rows = []
    for name, label in (("ranked_7300", "Рейтинг 7 300+"), ("professional", "Про-матчи")):
        entry = data["groups"][name]
        rows.append((label, entry["players"] if entry else 0) + tuple(
            number(entry[t][k]) if entry else "—" for t in ("at10", "at20") for k in ("lh","gold","xp")))
    rows.append(("Твой герой", 1) + tuple(number(data["at10"][m["key"]]) for m in data["metrics"]) +
                tuple(number(m["actual"]) for m in data["metrics"]))
    summary = table(("Группа", "Выступления", "Крипы 10:00", "Золото 10:00", "Опыт 10:00",
                     "Крипы 20:00", "Золото 20:00", "Опыт 20:00"), rows)
    if data["metrics"][0]["expected"] is None:
        return intro + summary + '<p>Для проверки ML по времени пока мало завершённых матчей этого патча.</p>'
    forecast = table(("К 20:00", "Факт", "Ориентир ML", "Ошибка на проверке"), [
        (m["label"], number(m["actual"]), number(m["expected"]), number(m["mae"])) for m in data["metrics"]])
    return intro + summary + ('<p>ML оценивает, сколько ресурсов такой герой обычно получает к 20:00 '
                              'при твоих показателях на 10:00. Ошибка измерена на более поздних картах до итогового обучения.</p>') + forecast


def lessons_view(lessons):
    links = []
    for lesson in lessons:
        url = lesson["url"]
        if urlsplit(url).scheme != "https":
            continue
        kinds = {"chapter_note": "По главе видео", "subtitle": "Фрагмент субтитров",
                 "transcript_note": "Заметка по субтитрам", "description_note": "По описанию выпуска",
                 "licensed_note": "По учебнику ModDota", "editorial_note": "Заметка по разбору",
                 "local_text": "Текст урока"}
        label = kinds.get(lesson.get("kind"), "Учебный материал")
        stamp = lesson.get("start")
        minute = f' · время в видео {int(stamp)//60}:{int(stamp)%60:02d}' if stamp is not None else ''
        links.append(f'<div class="resource"><a href="{h(url)}">{h(lesson["title"])}</a>'
                     f'<small>{label}{minute}</small><p>{h(lesson["text"])}</p></div>')
    return ('<details class="resources" open><summary>Материалы к этому эпизоду</summary>'
            + ''.join(links) + '</details>') if links else ''


def item_options(fight, position):
    candidates = fight["items"]
    if not candidates:
        return '<p class="meta">Для этого эпизода данных на конкретный предмет недостаточно.</p>'
    categories = list(dict.fromkeys(i["category"] for i in candidates))
    def preference(item):
        available = item["status"] in ("used", "prior_purchase")
        stat = item.get("pro")
        rate = stat["bought"] / stat["n"] if stat else 0
        return (categories.index(item["category"]), not available, -rate)
    ordered = sorted(candidates, key=preference)
    # Present both an existing tool and an alternative when the replay supports both.
    lead = [ordered[0]]
    alternative = next((i for i in ordered if i["category"] == lead[0]["category"]
                        and i["status"] in ("unseen", "later_purchase") and i is not lead[0]), None)
    if alternative:
        lead.append(alternative)
    def render(item):
        status = {"used": "Применён в драке", "prior_purchase": "Куплен до драки",
                  "during_purchase": "Куплен во время драки", "later_purchase": "Куплен позднее",
                  "unseen": "Вариант покупки", "unknown_timing": "Нужно проверить время получения"}[item["status"]]
        cost = (f' · справочная цена {number(item["cost"])} золота' if item["status"] in ("unseen", "later_purchase")
                and item.get("cost") else "")
        html = (f'<div class="item-option"><b>{h(item["name"])}</b><small>{status}{cost}. '
                f'Угроза — {h(item["threat_hero"])} · {h(item["ability"])}</small>'
                f'<p>{h(item["rationale"])}</p><small>{h(item["evidence"])}</small>')
        stat = item.get("pro")
        if stat:
            scope = " на твоём герое" if stat["same_hero"] else ""
            html += (f'<small>В более ранних про-играх этого патча до {_clock(fight["start"])} покупка записана '
                     f'в {stat["bought"]} из {stat["n"]} выступлений{scope} на позиции {int(position)} '
                     f'против {h(" / ".join(stat["opponents"]))}.')
            if stat["bought"] >= 3:
                html += f' Медиана первой покупки у успевших к этому времени — {_clock(stat["median"])}.'
            if stat["example"]:
                html += f' <a href="https://www.opendota.com/matches/{int(stat["example"])}/purchases">Пример покупки</a>'
            html += '</small>'
        activation = item.get("fight_pro")
        if activation:
            html += (f'<small>Применение предмета отмечено в {activation["uses"]} из {activation["n"]} сопоставимых '
                     f'про-драк на этой позиции, где его покупка уже была записана. У {h(activation["enemy"])} '
                     f'в этих окнах отмечено {h(activation["threat"])}. Допуск по началу боя — пять минут.</small>')
        return html + f'<small><a href="{h(item["source_url"])}">Описание предмета у Valve</a></small></div>'
    more = [i for i in ordered if i not in lead]
    parts = ['<h3>Что мог дать предмет</h3>', *(render(i) for i in lead)]
    if more:
        parts.append('<details class="resources"><summary>Другие варианты и ограничения</summary>' +
                     ''.join(render(i) for i in more) + '</details>')
    return ''.join(parts)


def peer_action(fight):
    pro = fight.get("pro_action")
    if not pro:
        return ""
    return (f'<p class="meta">В {pro["count"]} из {pro["n"]} сопоставимых про-драк на этом герое и позиции '
            f'записано применение {h(pro["ability"])}. В той же драке {h(pro["enemy"])} '
            f'применял {h(pro["threat"])}. Сравнение охватывает бои на том же патче с разницей '
            f'до пяти минут по времени начала. <a href="https://www.opendota.com/matches/{int(pro["example"])}/teamfights">'
            f'Пример на {_clock(pro["example_start"])}</a>.</p>')


def replay_checks(result):
    labels = {"yes": "Да", "no": "Нет", "unknown": "Мало данных"}
    rows = []
    for check in result.get("checks", []):
        value = labels.get(check["state"], "Мало данных")
        rows.append(f'<li><strong>{h(check["label"])} · {value}</strong> — {h(check["detail"])}</li>')
    return '<ul class="replay-checks">' + "".join(rows) + '</ul>' if rows else ''


def excluded_section(choices):
    if not choices:
        return ''
    parts = ['<section><h2>Какие варианты отпали при проверке</h2>']
    for d in choices:
        v = d["verification"]
        stamp = _clock(v["time"]) if v.get("time") is not None else f'{_clock(d["start"])}–{_clock(d["end"])}'
        parts.append(f'<details class="battle"><summary>{h(stamp)} · {h(d["button"])} → {h(d["target"])}</summary>'
                     f'<p>{h(v["reason"])}</p>{replay_checks(v)}</details>')
    return ''.join([*parts, '</section>'])


def decisions_section(decisions, *, checked=False):
    headings = ("Решения в драках", "Один момент для пересмотра", "Два главных момента", "Три главных момента")
    parts = [f'<section id="decisions" aria-labelledby="decisions-title"><h2 id="decisions-title">{headings[len(decisions)]}</h2>']
    if not decisions:
        if checked:
            return parts[0] + '<p>Повтор проверен. Для этого героя не нашлось эпизода, в котором все условия для совета подтверждены в одну секунду.</p></section>'
        return (parts[0] + '<p>В журнале не нашлось эпизода, где можно связать действие конкретного героя '
                'с подходящей целью и кнопкой. Если OpenDota ещё обрабатывает повтор, обнови состав матча позже.</p></section>')
    ordering = "Сначала — самый важный эпизод. " if len(decisions) > 1 else ""
    intro = ("Секунда нажатия взята из кадра повтора Valve. Видимость означает обзор твоей команды. "
             "Положение камеры игрока отдельно не оценивалось." if checked else
             "Время события взято из журнала. Проверь в повторе, была ли видна цель и готова ли кнопка перед этим моментом.")
    parts.append(f'<p class="meta">{ordering}{intro}</p><ol class="decision-list">')
    for rank, d in enumerate(decisions, start=1):
        start, end = d["start"], d["end"]
        interval = f'{_clock(start)}–{_clock(end)}'
        event_time = d["time"]
        timing = f'Событие на {_clock(event_time)}' if event_time is not None else f'Интервал {interval}'
        v = d.get("verification")
        side = d.get("recipient_side")
        action_target = {"self": "Применить на себя", "ally": "Кого прикрыть", "enemy": "Кого фокусить"}.get(side, "Цель действия")
        title = d.get("title") or {"interrupt": "Прервать каст", "focus": "Сфокусировать соперника",
                                   "protect": "Прикрыть союзника", "self_defense": "Защитить себя"}.get(d["kind"], "Выбор в драке")
        heading = f'{_clock(v["time"])} · {title}' if v else title
        parts.append(f'<li class="decision-card"><div class="decision-title"><span class="rank" aria-hidden="true">{rank}.</span>'
                     f'<h3>{h(heading)}</h3></div><p><span class="stamp">{h(timing)}</span> · {h(d["event"])}</p>'
                     f'<dl class="decision-tools"><div><dt>{action_target}</dt><dd>{h(d["target"])}</dd></div>'
                     f'<div><dt>Что нажать</dt><dd>{h(d["button"])}</dd></div>')
        enemy = d.get("enemy_focus")
        if enemy and enemy != d["target"]:
            parts.append(f'<div><dt>Источник угрозы</dt><dd>{h(enemy)}</dd></div>')
        parts.append('</dl>')
        if v:
            parts.append(f'<p><b>Можно было нажать на {_clock(v["time"])}.</b> {h(v["reason"])}</p>{replay_checks(v)}')
        elif d.get("timing"):
            parts.append(f'<p><b>Когда нажать.</b> {h(d["timing"])}</p>')
        if not v:
            parts.append(f'<p>{h(d["action"])}</p>')
        parts.append(f'<p><b>Почему.</b> {h(d["why"])}</p>')
        if not v and d.get("strength") == 1:
            parts.append('<p class="meta">У этой версии мало подтверждений. Сначала проверь цель и порядок действий в повторе.</p>')
        parts.append('<details class="decision-review"><summary>Что видно в журнале и что проверить</summary>'
                     f'<p>{h(d["evidence"])}</p>')
        if d.get("importance"):
            parts.append(f'<p>{h(d["importance"])}</p>')
        if not v:
            parts.append(f'<p>{h(d["check"])}</p>')
        if d.get("pro_note"):
            parts.append(f'<p>{h(d["pro_note"])}')
            if d.get("pro_url") and urlsplit(d["pro_url"]).scheme == "https":
                parts.append(f' <a href="{h(d["pro_url"])}">Про-матч для сравнения</a>')
            parts.append('</p>')
        parts.append('</details><p class="decision-links">')
        if d.get("review_from") is not None:
            parts.append(f'<span>Начни просмотр с {_clock(d["review_from"])}. </span>')
        parts.append(f'<a href="#fight-{int(start)}-{int(end)}">Разбор драки {h(interval)}</a></p></li>')
    parts.append('</ol></section>')
    return ''.join(parts)


def battle_timeline(session):
    fights = session["fights"]
    if not fights:
        return ''
    selected = session["selected_fight"]
    description = ("Доступность действия проверена по состоянию героев раз в игровую секунду. "
                   "Вверху показаны варианты, прошедшие все проверки." if session.get("replay_checked") else
                   "Открой интервал, чтобы посмотреть решение и предметы. Время убийства или смерти "
                   "берётся из журнала. Для большинства заклинаний OpenDota даёт число применений за весь интервал.")
    parts = ['<section id="fight-timeline"><h2>Драки по времени</h2>', f'<p class="meta">{description}</p>']
    for fight in fights:
        summary = f'{_clock(fight["start"])}–{_clock(fight["end"])}'
        named = fight["threats"][0] if fight["threats"] else None
        if named:
            summary += f' · {named["hero"]} — {named["ability"]}'
        if session["focus"] != "auto" and fight is selected and session["cards"] and session["cards"][0]["topic"] == "fights":
            parts.append(f'<p class="battle"><a href="#selected-fight">{h(summary)}</a> · выбранный эпизод</p>')
            continue
        facts = ''.join(f'<li>{h(f)}</li>' for f in fight["observations"])
        parts.append(f'<details class="battle"><summary>{h(summary)}</summary><div class="battle-body" id="fight-{int(fight["start"])}-{int(fight["end"])}">'
                     f'<span class="stamp">{h(_clock(fight["anchor"]))}</span> · {h(fight["anchor_text"])}<ul>{facts}</ul>')
        if not session.get("replay_checked"):
            parts.append(f'<p><b>Вариант действия.</b> {h(fight["action"])}</p><p>{h(fight["check"])}</p>'
                         f'{peer_action(fight)}{item_options(fight, session["position"])}')
        parts.append(f'{lessons_view(fight["lessons"])}</div></details>')
    parts.append('</section>')
    return ''.join(parts)


def progress_section(history):
    pts = history["actual"]
    if not history["prior"]:
        return ('<p>Это первая доступная точка сравнения. Разбери следующую игру того же аккаунта в той же роли и режиме '
                'в этой локальной школе, чтобы сравнить динамику.</p>' if history["identified"] else
                '<p>Аккаунт скрыт. По слоту можно разобрать эту карту. Для сравнения разных карт нужен известный аккаунт.</p>')
    rows = []
    for k, label in (("lh","Добивания"), ("gold","Золото"), ("xp","Опыт")):
        sample = [s[k] for _, s in history["prior"] if s[k] is not None]
        rows.append((label, number(pts[k]), number(median(sample) if sample else None), len(sample)))
    return ('<p>Прирост с 10:00 по 20:00 в этой карте и до пяти ранее разобранных играх того же аккаунта '
            'на той же позиции в этом режиме.</p>' +
            table(("Показатель", "Эта игра", "Медиана прошлых", "Карт с данными"), rows))


def render_session(session, *, home_url=None, detail_url=None):
    row, cards = session["row"], session["cards"]
    hero = row.get("hero_name") or f'Герой #{row["hero_id"]}'
    pos = session["position"]
    role_source = "выбрана игроком" if session["role_override"] else "по оценке OpenDota"
    role = f'Позиция {pos} {role_source} · ' if pos else ''
    result = {True:"Победа", False:"Поражение"}.get(row.get("win"), "Исход неизвестен")
    mid = int(row["match_id"])
    header = (f'<header><p class="eyebrow">PRO REPLAY LAB / ТРЕНИРОВКА ПО МАТЧУ {mid}</p>'
              f'<h1>{h(hero)}</h1><p class="meta">{role}{result} · {h(_date(row.get("start_time")))} · '
              f'K/D/A {number(row["kills"])} / {number(row["deaths"])} / {number(row["assists"])}</p></header>')
    parts = [header]
    auto = session["focus"] == "auto" and session["at"] is None
    has_ranked_fights = auto and bool(session.get("mistakes"))
    if auto:
        if session.get("replay_message"):
            parts.append(f'<p role="note"><strong>Разбор по журналу OpenDota.</strong> {h(session["replay_message"])} '
                         'Здесь показаны гипотезы. Проверка кнопки, командного обзора и дистанции по файлу недоступна.</p>')
        parts.append(decisions_section(session.get("mistakes", []), checked=session.get("replay_checked", False)))
        parts.append(excluded_section(session.get("excluded", [])))
    elif session["fights"]:
        parts.append(f'<nav><a href="#fight-timeline">Драки по времени ({len(session["fights"])})</a></nav>')
    if session["at"] is not None and session["selected_fight"] is None:
        parts.append(f'<section class="panel"><h2>На {_clock(session["at"])} нет размеченной драки для разбора</h2>'
                     '<p>Выбери соседний интервал с записями игрока или другой тайминг.</p></section>')
    elif not session["requested_available"]:
        parts.append(f'<p class="meta">Для темы «{h(FOCUSES[session["focus"]])}» недостаточно записей. Показаны эпизоды с данными.</p>')
    if cards:
        lead = cards[0]
        battle = session["selected_fight"] if lead["topic"] == "fights" else None
        fight_body = ""
        if battle and not has_ranked_fights:
            requested = f'Выбранное время {_clock(session["at"])}. ' if session["at"] is not None else ""
            fight_body = (f'<p class="meta">{requested}Интервал драки {_clock(battle["start"])}–{_clock(battle["end"])}</p>'
                          + peer_action(battle) + item_options(battle, pos))
        if not has_ranked_fights:
            parts.append(f'<section class="focus" id="selected-fight"><p class="eyebrow">ФОКУС НА ЭТУ ТРЕНИРОВКУ</p>'
                         f'<h2>{h(lead["title"])}</h2><p><span class="stamp">{h(lead["time"])}</span> · {h(lead["evidence"])}</p>'
                         f'<p>{h(lead["review"])}</p>{fight_body}</section>')
        parts.append(f'<section class="exercise"><h3>Задание на следующую игру</h3>'
                     f'<p>{h(lead["drill"])}</p><p><b>Как проверить.</b> {h(lead["check"])}</p></section>')
        if home_url:
            if (session["history"].get("saved") == lead["topic"] and
                    session["history"].get("saved_content") == (lead["drill"], lead["check"])):
                parts.append('<p role="status">Задание сохранено в твоём локальном плане.</p>')
            else:
                carry = ""
                if battle and not auto:
                    chosen = session["at"] if session["at"] is not None else battle["anchor"]
                    carry += f'<input type="hidden" name="at" value="{h(_clock(chosen))}">'
                if session["role_override"]:
                    carry += f'<input type="hidden" name="role" value="{int(pos)}">'
                parts.append(f'<form method="post" action="/practice">{carry}<input type="hidden" name="match" value="{mid}">'
                             f'<input type="hidden" name="slot" value="{int(row["player_slot"])}">'
                             f'<input type="hidden" name="focus" value="{h("auto" if auto else lead["topic"])}">'
                             '<button type="submit">Взять задание на следующую игру</button></form>')
        if lead.get("lessons"):
            parts.append(lessons_view(lead["lessons"]))
        if not has_ranked_fights and len(cards)>1:
            parts.append('<section><h2>Ещё эпизоды для просмотра</h2>')
            for c in cards[1:]:
                parts.append(f'<article class="episode"><h3><span class="stamp">{h(c["time"])}</span>{h(c["title"])}</h3>'
                             f'<p>{h(c["evidence"])}</p><p>{h(c["review"])}</p>{lessons_view(c.get("lessons", []))}</article>')
            parts.append('</section>')
    elif session["at"] is None and not auto:
        parts.append('<section class="panel"><h2>Нужен разобранный повтор</h2><p>Карточка матча получена, но в ней пока нет '
                     'событий или снимков, на которых можно построить упражнение. После разбора повтора в OpenDota обнови карточку.</p></section>')
    parts.append(battle_timeline(session))
    if "patch_reference" in session:
        parts.append('<section><h2>Планки экономики на патче</h2>' + patch_benchmarks(session["patch_reference"]) + '</section>')
    parts.append('<details><summary>Прогноз экономики для твоего матча</summary>' + pro_section(session["assessment"]) + '</details>')
    previous = session["history"].get("previous")
    if previous:
        prior_id, drill, check = previous
        parts.append(f'<section class="exercise"><h2>Проверка прошлого задания</h2><p>После матча '
                     f'{int(prior_id)} ты выбрал задание «{h(drill)}»</p><p>{h(check)}</p></section>')
    parts.append('<section><h2>Твоя динамика</h2>'+progress_section(session["history"])+'</section>')
    links = [f'<a href="https://www.opendota.com/matches/{mid}">Источник событий в OpenDota</a>']
    if detail_url:
        links.append(f'<a href="{h(detail_url)}">Полный разбор драфта и драк</a>')
    if home_url:
        links.append(f'<a href="{h(home_url)}">Разобрать другой матч</a>')
    parts.append('<footer>' + ''.join(links) + '</footer>')
    return page(f'{hero} · тренировка по матчу {mid}', ''.join(parts))
