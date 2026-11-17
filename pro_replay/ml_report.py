"""Human-readable audit of the fixed model comparison."""

from .coach_report import h, page, table


LABELS = {"lh": "Добивания", "gold": "Золото", "xp": "Опыт"}
COHORTS = {"fresh_ranked": "Новые рейтинговые игры", "fresh_professional": "Новые профессиональные игры",
           "legacy_professional": "Прежние 100 про-игр", "legacy_ranked": "Прежние 119 рейтинговых игр"}


def fmt(value):
    return "—" if value is None else f"{value:,.2f}".replace(",", " ")


def render_benchmark(report, items=None):
    selection = report["selection"]
    winner = selection["winner"]
    ranked = report.get("format") == "ranked-client-benchmark-1"
    context = (f'Выбор сделан по {selection["development"]["games"]} ранее изученным рейтинговым матчам. '
               'Веса кандидатов обучены на тех же 400 про-играх. Новые проверочные данные открыты после фиксации выбора.'
               if ranked else 'Выбор сделан на трёх временных проверках внутри первых 400 про-матчей. Новые игры открыты после фиксации модели.')
    release = 'Условие выпуска выполнено.' if report["release_gate"]["passed"] else 'Проверка переноса не пройдена: этот вариант не включён по умолчанию.'
    body = (f'<header><p class="eyebrow">PRO REPLAY LAB · ML BENCHMARK</p><h1>Проверка прогнозов</h1>'
            f'<p>Выбранная модель: <strong>{h(winner)}</strong>. {context}</p><p>{release}</p></header>'
            '<section><h2>Сравнение на обучающей истории</h2><p>Меньшая ошибка лучше. '
            'Общий балл — среднее отношение трёх ошибок к простому прогнозу по месту в команде.</p>')
    body += table(("Вариант", "Общий балл", "Добивания", "Золото", "Опыт"), [
        (r["name"] + (" ← выбран" if r["name"] == winner else ""), fmt(r["score"])) +
        tuple(fmt(r["metrics"][k]["mae"]) for k in LABELS)
        for r in sorted(selection["leaderboard"], key=lambda r: r["score"])]) + '</section>'
    cohorts = {"independent_retrospective_ranked": "Независимая историческая проверка"} if ranked else COHORTS
    for cohort, title in cohorts.items():
        data = report["evaluation"][cohort]
        chosen, old = data["candidates"][winner], data["candidates"]["ridge_legacy"]
        body += f'<section><h2>{title}</h2><p>{data["games"]} матчей · {data["players"]} выступлений.</p>'
        if cohort == "fresh_professional":
            body += '<p>Выборка очень мала; результат предварительный.</p>'
        if ranked:
            body += '<p>Ранее не использованные матчи за 16 ноября. Это проверка на независимых исторических играх; перенос на будущие игры после подбора модели ещё не измерен.</p>'
        if cohort.startswith("legacy"):
            body += '<p>Эти матчи уже изучались ранее. Таблица служит повторным сравнением.</p>'
        rows = []
        for key, label in LABELS.items():
            before, after = old["metrics"][key]["mae"], chosen["metrics"][key]["mae"]
            ci = chosen["paired_against_ridge_legacy"]["targets"][key]["ci95"]
            rows.append((label, fmt(before), fmt(after), f'{100*(1-after/before):.1f}%',
                         ' … '.join(fmt(v) for v in ci) if ci else '—'))
        body += table(("Показатель", "Старая ошибка", "Новая ошибка", "Снижение", "95% интервал разницы ошибок"), rows)
        body += '<p class="meta">Отрицательная разница означает улучшение. Интервалы получены повторной выборкой целых матчей.</p>'
        bands = chosen.get("uncertainty")
        if bands:
            body += '<details><summary>Покрытие диапазонов прогноза</summary><p>Ширина задана по ошибкам внутри обучения. '
            body += '90% — ориентир, а не гарантия для новых игр.</p>'
            body += table(("Показатель", "Попало в диапазон", "Средняя ширина"), [
                (LABELS[k], f'{v["empirical_coverage"]:.1%}', fmt(v["mean_width"])) for k, v in bands["targets"].items()]) + '</details>'
        errors = chosen["error_analysis"]
        body += '<details><summary>Где модель ошибается</summary>'
        body += table(("Группа", "Матчи", "Игроки", "Ошибка добиваний", "Ошибка золота", "Ошибка опыта"), [
            (name, value["games"], value["players"]) + tuple(fmt(value["metrics"][k]["mae"]) for k in LABELS)
            for name, value in errors["slices"].items()]) + '</details>'
        body += '<details><summary>Самые большие промахи по золоту</summary><ul>'
        for err in errors["largest_absolute_errors"]["gold"][:5]:
            body += (f'<li><a href="{h(err["match_url"])}">Матч {err["match_id"]}</a>, герой {err["hero"]}: '
                     f'прогноз {fmt(err["predicted"])}, факт {fmt(err["actual"])}, ошибка {fmt(err["absolute_error"])}.</li>')
        body += '</ul></details></section>'
    if items:
        body += '<section><h2>Следующий записанный предмет</h2><p>100 матчей Yatoro: 80 для разработки, 20 для ранее опубликованной проверки.</p>'
        body += table(("Модель", "Временная проверка", "Последние 20", "Попал в топ-3"), [
            (r["recipe"]["name"], f'{r["validation"]["accuracy"]:.0%}', f'{r["legacy_test"]["accuracy"]:.0%}',
             f'{r["legacy_test"]["top3_accuracy"]:.0%}') for r in items["comparison"]])
        body += '<p>Предсказывается наблюдавшаяся покупка. Эти данные не доказывают, что предмет был лучшим против драфта.</p></section>'
    body += ('<section><h2>Границы результата</h2><p>Первая выборка новых рейтинговых игр собрана в узком окне около 52 минут '
             'на одном патче. Исключены короткие игры и игры с отключениями. На другие патчи и условия '
             'результат ещё не проверен. Обучение оптимальному фокусу в драке и влияние советов на MMR '
             'этим экспериментом не измерены.</p></section>')
    return page("Сравнение ML-моделей", body)
