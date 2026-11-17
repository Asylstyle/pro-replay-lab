"""Player actions, team totals and timed evidence in OpenDota teamfight windows."""

import re

from .analysis import _int
from .events import _LANES, _TOWER
from .report import _clock, _h, _item


SLOTS = (0, 1, 2, 3, 4, 128, 129, 130, 131, 132)
PREFIX = "npc_dota_hero_"
_BARRACKS = re.compile(r"npc_dota_(goodguys|badguys)_(melee|range)_rax_(top|mid|bot)")


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _counts(raw):
    if not isinstance(raw, dict) or any(not isinstance(k, str) or _count(v) is None for k, v in raw.items()):
        return None
    return {k: n for k, n in raw.items() if n > 0}


def _totals(team):
    totals = {}
    for field in ("damage", "healing", "deaths", "gold_delta"):
        values = [p.get(field) if isinstance(p, dict) else None for p in team]
        valid = all(type(v) is int and (field == "gold_delta" or v >= 0) for v in values)
        totals[field] = sum(values) if valid else None
    return totals


def _unit_name(key, names):
    return names.get(key) or key.removeprefix(PREFIX).replace("_", " ").title()


def _timed_events(player, start, end, names):
    events = []
    for field, kind in (("kills_log", "kill"), ("deaths_log", "death"), ("buyback_log", "buyback")):
        log = player.get(field)
        for event in log if isinstance(log, list) else ():
            if not isinstance(event, dict) or type(event.get("time")) is not int or not start <= event["time"] <= end:
                continue
            key = event.get("key")
            target = _unit_name(key, names) if isinstance(key, str) and key.startswith(PREFIX) else None
            events.append({"time": event["time"], "kind": kind, "hero": target})
    return sorted(events, key=lambda e: e["time"])


def _objective(event, directory, hero_by_slot):
    kind = event.get("type")
    if kind == "building_kill":
        key = event.get("key")
        if not isinstance(key, str):
            return None
        tower, barracks = _TOWER.fullmatch(key), _BARRACKS.fullmatch(key)
        if not tower and not barracks:
            return None
        side, structure, lane = (tower or barracks).groups()
        side = "Radiant" if side == "goodguys" else "Dire"
        if tower:
            text = f"Упала башня T{structure} команды {side}"
        else:
            soldiers = "ближнего" if structure == "melee" else "дальнего"
            text = f"Разрушена казарма {soldiers} боя команды {side}"
        if lane:
            text += f" ({_LANES[lane]})"
        actor = event.get("unit")
        if isinstance(actor, str) and re.fullmatch(r"npc_dota_hero_[a-z0-9_]+", actor):
            text += " · последний удар — " + _unit_name(actor, directory)
        elif isinstance(actor, str) and re.fullmatch(r"npc_dota_creep_(goodguys|badguys)_(melee|ranged|flagbearer|siege)", actor):
            text += " · последний удар — крип"
        return text
    if kind == "CHAT_MESSAGE_ROSHAN_KILL":
        side = {2: "Radiant", 3: "Dire"}.get(event.get("team")) if type(event.get("team")) is int else None
        return f"Команда {side} убила Roshan" if side else "Roshan погиб · сторона неизвестна"
    if kind == "CHAT_MESSAGE_AEGIS":
        slot = event.get("player_slot")
        if type(slot) is not int or slot not in SLOTS:
            return "Подобран Aegis · получатель неизвестен"
        side = "Radiant" if slot < 128 else "Dire"
        hero = hero_by_slot.get(slot)
        return f"Aegis получил {hero} ({side})" if hero else f"Aegis получил игрок {side}"
    return None


def _after_fights(raw, intervals, directory, heroes):
    """Attach each objective once, outside fights, to the latest qualifying end."""
    log = raw.get("objectives")
    if not isinstance(log, list):
        return
    for fight in intervals:
        fight["objectives_after"] = []
    hero_by_slot = {p["player_slot"]: heroes.get(p.get("hero_id"), {}).get("localized_name")
                    for p in raw.get("players") or [] if isinstance(p, dict) and "player_slot" in p}
    # An interval with missing player stats still bounds the time between fights.
    windows = [(f["start"], f["end"]) for f in raw["teamfights"] if isinstance(f, dict)
               and type(f.get("start")) is int and type(f.get("end")) is int and f["start"] <= f["end"]]
    for event in log:
        if not isinstance(event, dict) or type(event.get("time")) is not int:
            continue
        second = event["time"]
        if any(start <= second <= end for start, end in windows):
            continue
        nearby = [(end, start) for start, end in windows if 0 < second - end <= 60]
        if not nearby:
            continue
        end, start = max(nearby)
        fight = next((f for f in intervals if f["start"] == start and f["end"] == end), None)
        label = _objective(event, directory, hero_by_slot)
        if fight is not None and label:
            fight["objectives_after"].append({"time": second, "label": label})
    for fight in intervals:
        fight["objectives_after"].sort(key=lambda e: e["time"])


def extract_fights(raw, slot, heroes):
    """Analyze all recorded intervals; the player array uses fixed Radiant/Dire slot order."""
    source = raw.get("teamfights")
    result = {"available": isinstance(source, list), "recorded": len(source) if isinstance(source, list) else 0,
              "intervals": []}
    if not result["available"] or type(slot) is not int or slot not in SLOTS:
        return result
    directory = {h["name"]: h["localized_name"] for h in heroes.values() if h.get("name") and h.get("localized_name")}
    players = raw.get("players") or []
    player = next((p for p in players if isinstance(p, dict) and p.get("player_slot") == slot), {})
    unit = heroes.get(player.get("hero_id"), {}).get("name", "")
    result["hero_prefix"] = unit.removeprefix(PREFIX)
    index = SLOTS.index(slot)
    for fight in source:
        if not isinstance(fight, dict):
            continue
        start, end, parts = fight.get("start"), fight.get("end"), fight.get("players")
        if (type(start) is not int or type(end) is not int or end < start or
                not isinstance(parts, list) or len(parts) != 10 or not isinstance(parts[index], dict)):
            continue
        selected = parts[index]
        team, other = (parts[:5], parts[5:]) if index < 5 else (parts[5:], parts[:5])
        victims = _counts(selected.get("killed"))
        kills = None if victims is None else {_unit_name(k, directory): n for k, n in victims.items()
                                               if k.startswith(PREFIX)}
        targets = []
        spells = selected.get("ability_targets")
        for ability, counts in spells.items() if isinstance(spells, dict) else ():
            if not isinstance(ability, str):
                continue
            for target, n in (_counts(counts) or {}).items():
                if target.startswith(PREFIX):
                    targets.append({"ability": ability, "hero": _unit_name(target, directory), "count": n})
        result["intervals"].append({
            "start": start, "end": end,
            "team": _totals(team), "enemy": _totals(other),
            "damage": _count(selected.get("damage")), "healing": _count(selected.get("healing")),
            "deaths": _count(selected.get("deaths")), "buybacks": _count(selected.get("buybacks")),
            "gold_delta": _int(selected.get("gold_delta")),
            "kills": kills, "abilities": _counts(selected.get("ability_uses")),
            "items": _counts(selected.get("item_uses")), "targets": targets,
            "events": _timed_events(player, start, end, directory),
        })
    _after_fights(raw, result["intervals"], directory, heroes)
    return result


def _number(value):
    return "—" if value is None else f"{value:,}".replace(",", " ")


def _summary_counts(counts, name):
    if counts is None:
        return "нет данных"
    if not counts:
        return "0"
    return ", ".join(f"{_h(name(key))} ×{n}" for key, n in sorted(counts.items(), key=lambda p: (-p[1], p[0])))


def _spell_name(key, hero_prefix):
    if hero_prefix:
        key = key.removeprefix(hero_prefix + "_")
    return key.replace("_", " ").title()


def _render_interval(fight, hero_prefix):
    team, enemy = fight["team"], fight["enemy"]
    time = f'{_clock(fight["start"])}–{_clock(fight["end"])}'
    summary = (f'{time} · Смерти союзников {_number(team["deaths"])} · '
               f'Смерти противников {_number(enemy["deaths"])}')
    rows = []
    for key, title in (("damage", "Урон"), ("healing", "Лечение"), ("deaths", "Смерти"), ("gold_delta", "Изменение золота")):
        value, total = fight[key], team[key]
        share = "—"
        if key in ("damage", "healing") and value is not None and total:
            share = f"{100 * value / total:.1f}%".replace(".", ",")
        rows.append(f"<tr><td>{title}</td><td>{_number(value)}</td><td>{_number(total)}</td>"
                    f"<td>{share}</td><td>{_number(enemy[key])}</td></tr>")
    facts = [
        '<div class="scroll"><table><thead><tr><th>Показатель</th><th>Игрок</th><th>Своя команда</th>'
        '<th>Доля игрока</th><th>Противники</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>",
        '<p><b>Способности</b> · ' + _summary_counts(fight["abilities"], lambda k: _spell_name(k, hero_prefix)) + '</p>',
        '<p><b>Предметы</b> · ' + _summary_counts(fight["items"], _item) + '</p>',
        '<p><b>Убитые герои</b> · ' + _summary_counts(fight["kills"], str) +
        f' · <b>Выкупы игрока</b> · {_number(fight["buybacks"])}</p>',
    ]
    if fight["targets"]:
        target_list = ", ".join(f'{_h(_spell_name(t["ability"], hero_prefix))} → {_h(t["hero"])} ×{t["count"]}'
                                for t in fight["targets"])
        facts.append("<p><b>Записанные цели способностей</b> · " + target_list + "</p>")
    labels = {"kill": "Убийство", "death": "Смерть игрока", "buyback": "Выкуп"}
    events = []
    for e in fight["events"]:
        detail = labels[e["kind"]]
        if e["hero"]:
            detail += " · " + ("убийца " if e["kind"] == "death" else "") + _h(e["hero"])
        events.append(f'<li><time>{_clock(e["time"])}</time><span>{detail}</span></li>')
    if events:
        facts.append('<ul class="events">' + "".join(events) + "</ul>")
    after = fight.get("objectives_after")
    if after:
        objective_rows = "".join(f'<li><time>{_clock(e["time"])}</time><span>{_h(e["label"])} '
                                 f'(+{e["time"] - fight["end"]} с)</span></li>' for e in after)
        facts.append('<p><b>Объекты после драки</b></p><ul class="events">' + objective_rows + "</ul>")
    elif after == []:
        facts.append('<p class="meta">Для этой драки в выбранном окне нет записей об объектах.</p>')
    return "<details><summary>" + summary + "</summary>" + "".join(facts) + "</details>"


def render_fights(data):
    if not data or not data.get("available"):
        return "<p>Журнал командных драк отсутствует.</p>"
    fights = data["intervals"]
    if not data["recorded"]:
        return "<p>OpenDota вернул пустой журнал командных драк.</p>"
    if not fights:
        return "<p>В журнале отсутствуют интервалы с доступной строкой выбранного игрока.</p>"
    text = (f'<p>Доступно интервалов с данными игрока — {len(fights)} из {data["recorded"]}. '
            'Открой интервал для сравнения с обеими пятёрками и просмотра действий.</p>'
            '<p class="meta">OpenDota объединяет близкие по времени смерти и выбирает серии от трёх смертей героев. '
            'В этот интервал попадают события на всей карте. Лечение может включать восстановление здоровья самого героя. '
            'Объект в следующую минуту отнесён к ближайшей завершённой драке по времени. '
            'События внутри другой драки пропускаются. Последний удар указан по журналу. '
            'Цель способности приводится при явной записи в журнале. Для установления спасения союзника, '
            'точного порядка применений предметов или роли в инициации потребуется просмотр повтора. '
            '<a href="https://github.com/odota/parser/blob/master/processors/processTeamfights.mjs">Методика OpenDota</a></p>')
    if not any("objectives_after" in fight for fight in fights):
        text += '<p class="meta">Для этой карты нет данных о последовательности объектов после драк.</p>'
    for field, label in (("damage", "урон"), ("healing", "лечение")):
        measured = [f for f in fights if f[field] is not None and f[field] > 0]
        if measured:
            peak = max(measured, key=lambda f: f[field])
            total = peak["team"][field]
            value = _number(peak[field])
            if total:
                share = f'{100 * peak[field] / total:.1f}%'.replace(".", ",")
                value += f' из {_number(total)} у команды ({share})'
            text += (f'<p class="fact">Пик игрока по показателю «{label}» среди измеренных интервалов '
                     f'пришёлся на <b>{_clock(peak["start"])}–{_clock(peak["end"])}</b> · {value}.</p>')
    return text + "".join(_render_interval(f, data["hero_prefix"]) for f in fights)
