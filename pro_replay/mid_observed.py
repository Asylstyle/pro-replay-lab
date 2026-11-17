"""Short, nonoverlapping samples of actual two-way damage between the lane opponents."""

from collections import Counter
from html import escape
from math import isfinite

from .report import _clock


def observed_exchanges(events, slot, enemy, lane_ticks, spell_names):
    rows = []
    for e in events:
        if not isinstance(e, dict) or e.get("kind") not in ("attack_damage", "spell_damage"):
            continue
        if e.get("direct_hero_source") is False:
            continue
        when, tick, amount = e.get("game_time"), e.get("tick"), e.get("damage")
        if (type(when) not in (float, int) or not isfinite(when) or not 0 <= when <= 600
                or type(tick) is not int or type(amount) is not int or amount <= 0):
            continue
        actors = (e.get("actor_slot"), e.get("target_slot"))
        if actors not in ((slot, enemy), (enemy, slot)):
            continue
        # Use the newest preceding observation; a later mid frame cannot place
        # an earlier hit on the lane. A missing second also breaks continuity.
        second = int(when)
        frame_tick = lane_ticks.get(second)
        if frame_tick is None or (tick < frame_tick and not 0 <= lane_ticks.get(second - 1, tick + 1) <= tick):
            continue
        ability = "Обычная атака"
        if e["kind"] == "spell_damage":
            key = e.get("button_key")
            if not isinstance(key, str) or not key:
                continue
            ability = spell_names.get(key, key.replace("_", " "))
        rows.append((when, tick, actors[0], ability, amount))
    rows.sort(key=lambda r: (r[0], r[1]))
    groups = []
    start = 0
    while start < len(rows):
        at = rows[start][0]
        stop = start + 1
        while stop < len(rows) and rows[stop][0] <= at + 3:
            stop += 1
        damage = {slot: Counter(), enemy: Counter()}
        for _, _, source, ability, amount in rows[start:stop]:
            damage[source][ability] += amount
        if damage[slot] and damage[enemy]:
            groups.append({"start": at, "end": rows[stop - 1][0],
                           "dealt": sum(damage[slot].values()), "received": sum(damage[enemy].values()),
                           "own_actions": dict(damage[slot]), "enemy_actions": dict(damage[enemy])})
        start = stop
    if len(groups) <= 3:
        return groups
    by_difference = sorted(groups, key=lambda g: g["dealt"] - g["received"])
    # Show examples from each end of the observed range and the earliest other episode.
    selected = [by_difference[0], by_difference[-1]]
    selected.append(next(g for g in groups if g not in selected))
    return sorted(selected, key=lambda g: g["start"])


def render_observed_exchanges(groups, enemy):
    if not groups:
        return ""
    parts = ["<details class=\"battle\"><summary>Сколько урона мидеры действительно нанесли друг другу</summary>",
             "<p>Суммы по перечисленным попаданиям двух мидеров у средней линии. В каждом примере от первого до последнего "
             "попадания прошло до трёх секунд. Урон крипов, башни и других героев, а также лечение "
             "и более поздние тики в эти суммы не входят. У героев с клонами журнал может объединять "
             "попадания под одним именем.</p><ul>"]
    for row in groups:
        def sources(key):
            return ", ".join(f"{escape(name)} ({amount})" for name, amount in row[key].items())
        first, last = _clock(int(row["start"])), _clock(int(row["end"]))
        stamp = first if first == last else f"{first}–{last}"
        parts.append(f'<li><b>{stamp}</b> · Ты нанёс {row["dealt"]} урона — {sources("own_actions")}. '
                     f'{escape(enemy)} нанёс {row["received"]} — {sources("enemy_actions")}.</li>')
    return "".join(parts) + "</ul></details>"
