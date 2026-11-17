"""Standalone client lesson and the shared shell for the local coaching app."""

from html import escape
from statistics import median
from urllib.parse import urlsplit

from .coaching import FOCUSES
from .report import _date


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
    details = table(("К 20:00", "Факт", "Ориентир модели", "Типичная ошибка¹"),
                    [(m["label"], number(m["actual"]), number(m["expected"]), number(m["mae"])) for m in metrics])
    origin = {"train": "Эта карта входила в обучение.", "holdout": "Эта карта входила в отложенную проверку.",
              "external": "Эта карта находится за пределами обучающей и проверочной выборок."}.get(data.get("cohort"), "")
    seen = data["hero_train_n"]
    hero_note = (f'Выступлений на этом герое в обучении — {seen}.' if seen else
                 'Герой впервые встретился модели. Ориентир построен по экономике и месту в команде.')
    return (f'<p>По золоту на 10:00 игрок занимал место {h(data["farm_rank_10"])} из пяти в своей команде. '
            f'Модель учитывает этот порядок и экономику на 10:00. {hero_note} {origin}</p>{details}'
            '<p class="meta">¹ Средняя абсолютная ошибка на более поздних турнирных матчах, в единицах строки. '
            'Турнирный ориентир помогает выбрать эпизод. Для объяснения разрыва нужно проверить маршрут, '
            'распределение фарма и действия соперника.</p>')


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
        minute = f' · {int(stamp)//60}:{int(stamp)%60:02d}' if stamp is not None else ''
        links.append(f'<div class="resource"><a href="{h(url)}">{h(lesson["title"])}</a>'
                     f'<small>{label}{minute}</small><p>{h(lesson["text"])}</p></div>')
    return ('<details class="resources" open><summary>Материалы к этому эпизоду</summary>'
            + ''.join(links) + '</details>') if links else ''


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
    pos = row["detail"]["build"].get("position")
    role = f'Позиция {pos} по оценке OpenDota · ' if pos else ''
    result = {True:"Победа", False:"Поражение"}.get(row.get("win"), "Исход неизвестен")
    mid = int(row["match_id"])
    header = (f'<header><p class="eyebrow">PRO REPLAY LAB / ТРЕНИРОВКА ПО МАТЧУ {mid}</p>'
              f'<h1>{h(hero)}</h1><p class="meta">{role}{result} · {h(_date(row.get("start_time")))} · '
              f'K/D/A {number(row["kills"])} / {number(row["deaths"])} / {number(row["assists"])}</p></header>')
    parts = [header]
    if not session["requested_available"]:
        parts.append(f'<p class="meta">Для темы «{h(FOCUSES[session["focus"]])}» недостаточно записей. Показаны эпизоды с данными.</p>')
    if cards:
        lead = cards[0]
        parts.append(f'<section class="focus"><p class="eyebrow">ФОКУС НА ЭТУ ТРЕНИРОВКУ</p>'
                     f'<h2>{h(lead["title"])}</h2><p><span class="stamp">{h(lead["time"])}</span> · {h(lead["evidence"])}</p>'
                     f'<p>{h(lead["review"])}</p></section><section class="exercise"><h3>Задание на следующую игру</h3>'
                     f'<p>{h(lead["drill"])}</p><p><b>Как проверить.</b> {h(lead["check"])}</p></section>')
        if home_url:
            if session["history"].get("saved") == lead["topic"]:
                parts.append('<p role="status">Задание сохранено в твоём локальном плане.</p>')
            else:
                parts.append(f'<form method="post" action="/practice"><input type="hidden" name="match" value="{mid}">'
                             f'<input type="hidden" name="slot" value="{int(row["player_slot"])}">'
                             f'<input type="hidden" name="focus" value="{h(lead["topic"])}">'
                             '<button type="submit">Взять задание на следующую игру</button></form>')
        if lead.get("lessons"):
            parts.append(lessons_view(lead["lessons"]))
        if len(cards)>1:
            parts.append('<section><h2>Ещё эпизоды для просмотра</h2>')
            for c in cards[1:]:
                parts.append(f'<article class="episode"><h3><span class="stamp">{h(c["time"])}</span>{h(c["title"])}</h3>'
                             f'<p>{h(c["evidence"])}</p><p>{h(c["review"])}</p>{lessons_view(c.get("lessons", []))}</article>')
            parts.append('</section>')
    else:
        parts.append('<section class="panel"><h2>Нужен разобранный повтор</h2><p>Карточка матча получена, но в ней пока нет '
                     'событий или снимков, на которых можно построить упражнение. После разбора повтора в OpenDota обнови карточку.</p></section>')
    parts.append('<section><h2>Ориентир по про-играм</h2>' + pro_section(session["assessment"]) + '</section>')
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
