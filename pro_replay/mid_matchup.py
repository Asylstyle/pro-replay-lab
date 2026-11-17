"""Compare mid-lane options at equal early levels with patch-specific Valve data."""

from functools import lru_cache
from html import escape
import json
from pathlib import Path
import time

from .patch_reference import _verified_calendar
from .reference_policy import resolve_patch
from .source import SourceError, valve_patches


LEVELS = (1, 2, 3, 4, 5, 6, 7)
DAMAGE_TYPE = {1: "физ.", 2: "маг.", 4: "чист."}
# Total XP for the comparison levels, pinned to the OpenDota constants in the guide.
XP = {2: 240, 3: 640, 4: 1160, 5: 1760, 6: 2440, 7: 3200, 12: 8200, 18: 17500}


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name("mid_matchup_data.json").read_text(encoding="utf8"))


def _mid(player):
    lane, role = player.get("lane"), player.get("lane_role")
    return (lane == 2 or role == 2) and lane in (None, 0, 2) and role in (None, 0, 2)


def _current_patch(raw, patch, data):
    start = raw.get("start_time")
    if type(start) is not int or not data["patch_start"] <= start <= int(time.time()):
        return False
    if patch is not None and patch != data["patch"]:
        return False
    if start <= data["checked_through"]:
        return True
    try:
        calendar, checked = _verified_calendar(int(time.time()) // 3600)
        if start > checked:
            calendar = valve_patches()
        return resolve_patch(calendar, start) == data["patch"]
    except (SourceError, ValueError):
        return False


def _hit(hero, level):
    attr = hero["primary_attr"]
    if attr == 3:
        # Valve includes the starting primary stat for single-attribute heroes,
        # but its universal hero damage_min/max are the pre-attribute values.
        coefficient = 0.45 * (1 + hero.get("universal_bonus_pct", 0) / 100)
        extra = coefficient * (sum(hero["attributes"]) + (level - 1) * sum(hero["gains"]))
    else:
        extra = (level - 1) * hero["gains"][attr]
    return [round(hero[key] + extra, 1) for key in ("damage_min", "damage_max")]


def _learned(player):
    # Only a log with explicit hero levels can establish when a point was spent.
    # The common OpenDota array gives order only: players can save points.
    events = player.get("ability_upgrades")
    if not isinstance(events, list) or not events:
        return None
    if any(not isinstance(e, dict) or type(e.get("ability")) is not int or
           type(e.get("level")) is not int or not 1 <= e["level"] <= 30 for e in events):
        return None
    order = player.get("ability_upgrades_arr")
    if isinstance(order, list) and [e["ability"] for e in events] != order:
        return None
    if any(b["level"] < a["level"] for a, b in zip(events, events[1:])):
        return None
    return events


def _level_windows(player):
    times, values = player.get("times"), player.get("xp_t")
    if not isinstance(times, list) or not isinstance(values, list) or len(times) != len(values):
        return {}
    samples = [(t, v) for t, v in zip(times, values) if type(t) is int and 0 <= t and
               type(v) is int and v >= 0]
    if any(t1 >= t2 or x1 > x2 for (t1, x1), (t2, x2) in zip(samples, samples[1:])):
        return {}
    result = {}
    for (begin, low), (end, high) in zip(samples, samples[1:]):
        for level, threshold in XP.items():
            if low < threshold <= high:
                result[level] = (begin, end)
    return result


def _hero_window(player, hero):
    events = _learned(player)
    order = player.get("ability_upgrades_arr") or []
    spells = hero["nukes"]
    # Prefer a reviewed spell the player actually chose early if several exist.
    opening = order[:6] if isinstance(order, list) else []
    spell = next((s for aid in opening for s in spells if aid == s["id"]), spells[0] if spells else None)
    levels = []
    for level in LEVELS:
        row = {"level": level, "attack": _hit(hero, level)}
        if spell:
            cap = (level + 1) // 2
            rank = cap
            observed = events is not None and level <= events[-1]["level"]
            if observed:
                rank = sum(e["ability"] == spell["id"] and e["level"] <= level for e in events)
                if rank > cap:
                    rank, observed = cap, False
            row.update(rank=rank, observed=observed, nuke_damage=spell["damage"][rank-1] if rank else None)
        levels.append(row)
    ultimate = hero.get("ultimate")
    ult_ranks = {}
    if ultimate and events:
        for level, cap in ((6, 1), (12, 2), (18, 3)):
            if events[-1]["level"] >= level:
                rank = sum(e["ability"] == ultimate["id"] and e["level"] <= level for e in events)
                if rank <= cap:
                    ult_ranks[level] = rank
    return {"id": player["hero_id"], "name": hero["name_loc"], "range": hero["attack_range"],
            "bat": hero["attack_rate"], "spell": spell, "levels": levels, "ultimate": ultimate,
            "ult_ranks": ult_ranks, "opening_pick": bool(spell and spell["id"] in opening),
            "level_windows": _level_windows(player)}


def mid_matchup_review(raw, slot, position=None, *, patch=None):
    players = [p for p in (raw.get("players") or []) if isinstance(p, dict) and
               type(p.get("player_slot")) is int and p["player_slot"] in (*range(5), *range(128, 133))]
    selected = [p for p in players if p["player_slot"] == slot]
    if len(selected) != 1:
        return None
    player = selected[0]
    if position not in (None, 2) or (position is None and (not _mid(player) or player.get("is_roaming"))):
        return None
    if raw.get("game_mode") not in (None, 1, 2, 3, 4, 22):
        return {"status": "unavailable", "reason": "Сравнение учитывает стандартные способности и темп набора опыта героя. Режим этой карты имеет другие правила."}
    uncertain = {"status": "unavailable", "reason": "Разметка линий не подтверждает одну пару мидеров в этом матче."}
    if (any(player.get(k) not in (None, 0, 2) for k in ("lane", "lane_role"))
            or player.get("is_roaming") or len({p["player_slot"] for p in players}) != len(players)):
        return uncertain
    enemies = [p for p in players if (p["player_slot"] < 128) != (slot < 128)]
    rivals = [p for p in enemies if _mid(p) and not p.get("is_roaming")]
    if len(enemies) != 5 or len(rivals) != 1 or any(
            _mid(p) and not p.get("is_roaming") for p in players if p not in enemies and p is not player):
        return uncertain
    data = catalog()
    if not _current_patch(raw, patch, data):
        return {"status": "unavailable", "reason": f"Числа сверены с Valve на {data['patch']}. Для патча этой игры подтверждения нет."}
    pair = [data["heroes"].get(str(p.get("hero_id"))) for p in (player, rivals[0])]
    if not all(pair):
        return {"status": "unavailable", "reason": "Для одного из двух героев нет подтверждённых данных Valve."}
    return {"status": "available", "patch": data["patch"], "self": _hero_window(player, pair[0]),
            "opponent": _hero_window(rivals[0], pair[1])}


def _num(value):
    return f"{value:g}".replace(".", ",")


def _spell_cell(row):
    if "rank" not in row:
        return "—"
    if not row["rank"]:
        return "Ещё не изучен"
    prefix = "ранг" if row["observed"] else "до ранга"
    return f"{prefix} {row['rank']} · {_num(row['nuke_damage'])}"


def _level_timing(ours, enemy):
    def clock(t):
        return f"{t//60}:{t%60:02d}"
    rows = []
    for level in (2, 3, 5, 6, 7, 12, 18):
        a, b = (h["level_windows"].get(level) for h in (ours, enemy))
        if not a or not b:
            continue
        if a[1] <= b[0]:
            first = escape(ours["name"])
        elif b[1] <= a[0]:
            first = escape(enemy["name"])
        else:
            first = "Порядок не виден"
        rows.append(f"<tr><td>{level}</td><td>{clock(a[0])}–{clock(a[1])}</td>"
                    f"<td>{clock(b[0])}–{clock(b[1])}</td><td>{first}</td></tr>")
    if not rows:
        return ""
    return ('<h3>Когда игроки получили уровень</h3><p>Интервалы по накопленному опыту в журнале. '
            'Если один мидер получил уровень раньше, пересмотри волну между этими отметками и проверь, '
            'было ли потрачено очко на усиление для размена.</p><div class="scroll"><table><thead>'
            f'<tr><th>Уровень</th><th>{escape(ours["name"])}</th><th>{escape(enemy["name"])}</th>'
            '<th>Кто раньше</th></tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>')


def _power_spikes(ours, enemy):
    out = ['<h3>Когда меняется преимущество</h3>']
    runs = []
    for a, b in zip(ours["levels"], enemy["levels"]):
        left, right = a["attack"], b["attack"]
        leader = ours["name"] if left[0] > right[1] else enemy["name"] if right[0] > left[1] else None
        if not runs or runs[-1][2] != leader:
            runs.append([a["level"], a["level"], leader])
        else:
            runs[-1][1] = a["level"]
    parts = []
    for begin, end, leader in runs:
        interval = str(begin) if begin == end else f"{begin}–{end}"
        verdict = f'выше весь диапазон у {escape(leader)}' if leader else 'диапазоны пересекаются'
        parts.append(f'{interval} ур. — {verdict}')
    out.append('<p><b>Обычный удар при равном уровне</b>. ' + '. '.join(parts) + '.</p>')
    for h in (ours, enemy):
        spell = h["spell"]
        if not spell:
            continue
        increments = [spell["damage"][rank] - spell["damage"][rank-1] for rank in (1, 2)]
        out.append(f'<p><b>{escape(h["name"])}</b> · при приоритетной прокачке {escape(spell["name"])} '
                   f'на 3-м уровне прибавляет {_num(increments[0])} урона к одному попаданию, '
                   f'на 5-м ещё {_num(increments[1])}. На 7-м доступен ранг 4 с уроном {_num(spell["damage"][3])}. '
                   'Если очко ушло в другую кнопку, окно сдвигается.</p>')
    for h in (ours, enemy):
        ult = h["ultimate"]
        if not ult:
            continue
        points = []
        for level, cap in ((6, 1), (12, 2), (18, 3)):
            rank = h["ult_ranks"].get(level)
            label = f'доступен ранг {cap}'
            if rank is not None:
                label = f'ранг {rank} в журнале' if rank else 'ещё не изучен'
            points.append(f'{level} ур. — {label}')
        out.append(f'<p><b>{escape(h["name"])} · {escape(ult["name"])}</b>. ' + '. '.join(points) + '.</p>')
    return ''.join(out)


def render_mid_matchup(review):
    if not review:
        return ""
    out = ['<section id="mid-matchup"><h2>Мид по уровням</h2>']
    if review["status"] != "available":
        return "".join(out) + f'<p>{escape(review["reason"])}</p></section>'
    ours, enemy = review["self"], review["opponent"]
    names = [escape(h["name"]) for h in (ours, enemy)]
    out.append(f'<p><b>{names[0]} против {names[1]}</b> · патч {escape(review["patch"])}. '
               'В каждой строке оба героя одного уровня. Удар указан приблизительно, до брони. '
               'Предметы, стаки, формы, проки и бонусы от прокачанных навыков могут изменить удар в матче.</p>')
    heads = "<th>Уровень</th>"
    for h, name in zip((ours, enemy), names):
        spell_name = escape(h["spell"]["name"]) if h["spell"] else "Нюк"
        heads += f"<th>{name} · удар</th><th>{name} · {spell_name}</th>"
    out.append('<div class="scroll"><table><thead><tr>' + heads + '</tr></thead><tbody>')
    for left, right in zip(ours["levels"], enemy["levels"]):
        out.append(f'<tr><td>{left["level"]}</td>')
        for row in (left, right):
            low, high = row["attack"]
            out.append(f'<td>{_num(low)}–{_num(high)}</td><td>{_spell_cell(row)}</td>')
        out.append('</tr>')
    out.append('</tbody></table></div><p>«До ранга» показывает возможную прокачку, если вкладывать очки в этот нюк при первой возможности. '
               '«Ранг» означает, что уровень изучения записан в журнале. Цифра урона относится к одному описанному попаданию до защиты цели.</p>')
    for h, name in zip((ours, enemy), names):
        note = f'Дальность атаки {_num(h["range"])}, базовое время атаки {_num(h["bat"])} с.'
        spell = h["spell"]
        if spell:
            note += f' {escape(spell["name"])} ({DAMAGE_TYPE[spell["damage_type"]]} урон). {escape(spell["note"])}'
            if spell["mana"] and spell["cooldown"]:
                note += f' На первом ранге расход маны {_num(spell["mana"][0])}, перезарядка {_num(spell["cooldown"][0])} с.'
            if h["opening_pick"]:
                note += ' Игрок вложил в этот навык одно из первых шести потраченных очков.'
        else:
            note += ' Для этого героя точный урон отдельного нюка пока не разобран.'
        url = f'https://www.dota2.com/datafeed/herodata?language=english&amp;hero_id={h["id"]}'
        out.append(f'<p><b>{name}.</b> {note} <a rel="noopener" href="{url}">Данные Valve</a></p>')
    out.append(_power_spikes(ours, enemy))
    out.append(_level_timing(ours, enemy))
    cue = ''
    if ours["range"] != enemy["range"]:
        longer, shorter = sorted((ours, enemy), key=lambda h: h["range"], reverse=True)
        cue += (f' У {escape(longer["name"])} дальность больше на {_num(longer["range"] - shorter["range"])}. '
                f'Проверь, мог ли {escape(shorter["name"])} добить спорного крипа, оставаясь за своей волной.')
    out.append(f'<p>{cue} На спорном дальнем крипе сравни оставшееся здоровье с уроном после брони. Учти летящие атаки, '
               'дальность и момент замаха. Если автоатака опоздает, проверь, хватало ли маны на нюк и мог ли он заодно задеть мидера. '
               'При размене учитывай защиту цели, позицию саппортов и возможный ответ после траты кнопки.</p></section>')
    return "".join(out)
