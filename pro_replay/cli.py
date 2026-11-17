"""Command-line player scouting and local ML experiments."""

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from .analysis import summarize
from .archive import Archive, normalize, pages, sync
from .events import summarize_events
from .learning import train_model, predict
from .patterns import find_order
from .report import render_report
from .match_report import render_match
from .impact import SLOTS
from .source import OpenDota, SourceError, valve_patches


EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "yatoro_case.json"


def save_text(path, text):
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=dest.parent, delete=False) as temp:
        name = Path(temp.name)
        try:
            temp.write(text)
        except BaseException:
            name.unlink(missing_ok=True)
            raise
    try:
        os.replace(name, dest)
    finally:
        name.unlink(missing_ok=True)


def mmss(value):
    minutes, seconds = divmod(abs(value), 60)
    return f"{'-' if value < 0 else ''}{minutes:02}:{seconds:02}"


def report(player, rows, dest, case=False, *, detailed=False, archive_rows=None, references=None):
    if detailed:
        dest = Path(dest)
        folder = dest.parent / (dest.stem + "-matches")
        enriched = []
        for row in rows:
            name = f'{int(row["match_id"])}.html'
            save_text(folder / name, render_match(row, archive_rows if archive_rows is not None else rows, references))
            enriched.append({**row, "_match_page": quote(folder.name) + "/" + name})
        rows = enriched
    stats = summarize(rows)
    stats["events"] = summarize_events(rows)
    if case:
        observed = find_order(rows, "tower", "item:manta")["found"]
        stats["case_note"] = " — ".join(["Башня до Manta Style"] + [
            f'{r["hero"]}: {mmss(r["before_time"])} → {mmss(r["after_time"])}' for r in observed
        ]) + ". Показаны выбранные события двух карт против TEAM VISION."
    save_text(dest, render_report(player, rows, stats))
    print(f"Отчёт: {Path(dest).resolve()} ({len(rows)} карт)")


def action(value):
    if value in {"tower","kill","observer","sentry"} or re.fullmatch(r"item:[a-z0-9_]+", value):
        return value
    raise argparse.ArgumentTypeError("Доступны tower, kill, observer, sentry, item:<ключ>.")


def positive(value):
    value = int(value)
    if value < 1:
        raise argparse.ArgumentTypeError("Введи положительное число.")
    return value


def parser():
    p = argparse.ArgumentParser(description="Поиск действий про-игрока в матчах Dota 2")
    p.add_argument("--db", default="data/archive.sqlite", help="SQLite-кэш матчей")
    sub = p.add_subparsers(dest="cmd", required=True)
    demo = sub.add_parser("demo", help="Офлайн-разбор двух реальных карт Yatoro")
    demo.add_argument("--out", default="yatoro.html")
    sub.add_parser("patches", help="Обновить локальный календарь патчей Valve")
    match = sub.add_parser("match", help="Разбор игрока в турнирной или рейтинговой игре")
    match.add_argument("player", help="Ник про-игрока, account ID или слот этой карты, например slot:130")
    match.add_argument("match_id", type=positive)
    match.add_argument("--out", type=Path, help="Путь для автономного HTML-отчёта")
    fetch = sub.add_parser("sync", help="Собрать матчи игрока из OpenDota")
    fetch.add_argument("player", help="Ник про-игрока или числовой account ID")
    group = fetch.add_mutually_exclusive_group()
    group.add_argument("--limit", type=positive, default=20, help="Последние N турнирных матчей (по умолчанию 20)")
    group.add_argument("--all", action="store_true", help="Вся доступная игроку история из Data Explorer")
    group.add_argument("--match", type=positive, nargs="+", help="Скачать только перечисленные турнирные матчи")
    fetch.add_argument("--refresh", action="store_true", help="Обновить в том числе сохранённые карты")
    imp = sub.add_parser("import", help="Импорт локальных ответов OpenDota GET /matches/{id}")
    imp.add_argument("player", type=positive, help="Числовой account ID")
    imp.add_argument("file", nargs="+", type=Path)
    imp.add_argument("--heroes", type=Path, help="Локальный справочник OpenDota GET /heroes в JSON")
    view = sub.add_parser("report", help="Построить HTML по загруженным матчам")
    view.add_argument("player")
    view.add_argument("--out", default="report.html")
    view.add_argument("--details", action="store_true", help="Добавить отдельную страницу с аналитикой каждой карты")
    find = sub.add_parser("find", help="Найти наблюдаемую последовательность событий")
    find.add_argument("player", help="Ник, ID или demo для примера Yatoro")
    find.add_argument("before", type=action)
    find.add_argument("after", type=action)
    find.add_argument("--within", type=positive, help="Максимальный интервал между событиями в секундах")
    train = sub.add_parser("train", help="Обучить и оценить модель на CPU")
    train.add_argument("player")
    train.add_argument("--out", default="data/training.json")
    for cmd in (view, find, train):
        cmd.add_argument("--hero", type=positive, help="Фильтр по ID героя")
        cmd.add_argument("--patch", type=positive, help="Фильтр по ID патча OpenDota")
    infer = sub.add_parser("predict", help="Прогноз по данным на 10:00")
    infer.add_argument("model", type=Path)
    for name in ("hero", "patch", "lh", "gold", "xp"):
        infer.add_argument("--" + name, type=int, required=True)
    infer.add_argument("--version", help="Версия из календаря Valve, например 7.41f")
    return p


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def review_one(archive, args):
    api = OpenDota()
    slot = None
    account = None
    if args.player.startswith("slot:"):
        token = args.player[5:]
        if not token.isascii() or not token.isdecimal() or int(token) not in SLOTS:
            raise ValueError("Слот Radiant от 0 до 4, слот Dire от 128 до 132.")
        slot = int(token)
    else:
        account, _ = archive.match_player(args.player, api)
    row = archive.cached_match(args.match_id, account, slot)
    if not row or row.get("review_version") != 2:
        try:
            raw = api.match(args.match_id)
            try:
                heroes = archive.hero_names(api)
            except SourceError:
                heroes = {}
            fresh = normalize(raw, account, heroes, slot=slot)
            if not fresh:
                raise ValueError("В этой карте не найден указанный игрок или слот.")
            archive.save(fresh)
            row = archive.with_patch_versions([fresh])[0]
        except SourceError:
            if not row:
                raise
            print("Источник недоступен. Показаны ранее сохранённые данные карты.")
    identifier = account if slot is None else f"slot-{slot}"
    path = args.out or Path(f"match-{args.match_id}-{identifier}.html")
    save_text(path, render_match(row, archive.personal_history(row), archive.references()))
    print(f"Разбор карты: {path.resolve()}")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.cmd == "demo":
            rows = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            report("Yatoro", rows, args.out, case=True)
            return 0
        if args.cmd == "predict":
            result = json.loads(args.model.read_text(encoding="utf-8"))
            if result.get("status") != "trained" or not result.get("model"):
                raise ValueError("Файл не содержит обученную модель.")
            if min(args.lh, args.gold, args.xp) < 0 or min(args.hero, args.patch) <= 0:
                raise ValueError("Проверь ID и показатели на 10:00.")
            print_json(predict(result["model"], {"hero_id": args.hero, "patch": args.patch, "lh_10": args.lh,
                                                "gold_10": args.gold, "xp_10": args.xp,
                                                "patch_version": args.version}))
            return 0
        archive = Archive(args.db)
        try:
            if args.cmd == "patches":
                entries = valve_patches()
                archive.save_patches(entries)
                print(f"Обновлён календарь Valve. Версий: {len(entries)}.")
                return 0
            if args.cmd == "match":
                review_one(archive, args)
                return 0
            if args.cmd == "sync":
                api = OpenDota()
                account, player = archive.match_player(args.player, api)
                ids = args.match if args.match else pages(api, account, None if args.all else args.limit)
                print_json(sync(archive, api, account, ids, args.refresh))
                print(f"Карт в кэше для {player}: {len(archive.rows(account))}")
                return 0
            if args.cmd == "import":
                directory = {i: {"name": unit, "localized_name": name}
                             for i, unit, name in archive.db.execute("SELECT id,unit,title FROM heroes")}
                if args.heroes:
                    heroes = json.loads(args.heroes.read_text(encoding="utf-8-sig"))
                    if not isinstance(heroes, list) or any(
                        not isinstance(h, dict) or type(h.get("id")) is not int
                        or not isinstance(h.get("name"), str) or not isinstance(h.get("localized_name"), str)
                        for h in heroes
                    ):
                        raise ValueError("Справочник должен соответствовать GET /heroes.")
                    directory.update({h["id"]: h for h in heroes})
                    with archive.db:
                        archive.db.executemany("INSERT OR REPLACE INTO heroes VALUES (?,?,?)",
                                               ((h["id"], h["name"], h["localized_name"]) for h in heroes))
                saved = set()
                for path in args.file:
                    raw = json.loads(path.read_text(encoding="utf-8-sig"))
                    for match in raw if isinstance(raw, list) else [raw]:
                        if not isinstance(match, dict) or not isinstance(match.get("match_id"), int):
                            raise ValueError(f"{path}: требуется JSON из OpenDota /matches/{{id}}.")
                        if not match.get("leagueid"):
                            continue
                        row = normalize(match, args.player, directory)
                        if row is not None:
                            archive.save(row)
                            saved.add(row["match_id"])
                print(f"Импортировано уникальных турнирных карт: {len(saved)}")
                return 0
            if args.cmd == "find" and args.player.casefold() == "demo":
                rows = json.loads(EXAMPLE.read_text(encoding="utf-8"))
                player = "Yatoro"
            else:
                account, player = archive.match_player(args.player)
                rows = archive.rows(account)
            all_rows = rows
            if args.hero:
                rows = [r for r in rows if r.get("hero_id") == args.hero]
            if args.patch:
                rows = [r for r in rows if r.get("patch") == args.patch]
            if not rows:
                raise ValueError("Нет подходящих матчей. Загрузи матчи через sync/import или проверь фильтры.")
            if args.cmd == "report":
                report(rows[0].get("player_name") or player, rows, args.out, detailed=args.details, archive_rows=all_rows,
                       references=archive.references() if args.details else None)
            elif args.cmd == "find":
                result = find_order(rows, args.before, args.after, args.within)
                print(f'Найдено: {len(result["found"])} / {len(rows)}. Без совпадения при доступных журналах: '
                      f'{len(result["absent"])}. Недостаточно данных: {len(result["unknown"])}.')
                for r in result["found"]:
                    print(f'{r["match_id"]}  {r["hero"]}  {mmss(r["before_time"])} → '
                          f'{mmss(r["after_time"])}  (+{mmss(r["gap"])})  {r["source"]}')
            elif args.cmd == "train":
                result = train_model(rows)
                print_json({k: v for k, v in result.items() if k != "model"})
                if result["status"] != "trained":
                    return 2
                result["account_id"] = account
                save_text(args.out, json.dumps(result, ensure_ascii=False, indent=2))
                print(f"Веса и результаты: {args.out}")
            return 0
        finally:
            archive.db.close()
    except (SourceError, OSError, ValueError, sqlite3.Error) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
