"""Draft-aware, auditable build review from one parsed OpenDota match."""

import json
from pathlib import Path

from .analysis import CORE_ITEMS, _int, _purchases
from .report import _clock, _h, _item


SLOTS = (0, 1, 2, 3, 4, 128, 129, 130, 131, 132)

# Frozen OpenDota constants. No network is needed when rendering reports.
with Path(__file__).with_name("item_catalog.json").open(encoding="utf8") as catalog:
    ITEM_CATALOG = json.load(catalog)["items"]
ITEMS = {number: entry[0] for number, entry in ITEM_CATALOG.items()}
ITEM_INFO = {key: (name, cost) for key, name, cost in ITEM_CATALOG.values()}


# (Relevant enemy hero, internal ability key, display name). Curated examples,
# not a claim that every ability or item on every patch is classified.
SPELLS = {
    "target": (
        (49, "dragon_knight_dragon_tail", "Dragon Tail"), (86, "rubick_telekinesis", "Telekinesis"),
        (26, "lion_voodoo", "Hex"), (27, "shadow_shaman_shackles", "Shackles"),
        (5, "crystal_maiden_frostbite", "Frostbite"), (27, "shadow_shaman_voodoo", "Hex"),
    ),
    "pierce": (
        (69, "doom_bringer_doom", "Doom"), (3, "bane_fiends_grip", "Fiend’s Grip"),
        (38, "beastmaster_primal_roar", "Primal Roar"), (104, "legion_commander_duel", "Duel"),
        (112, "winter_wyvern_winters_curse", "Winter’s Curse"),
    ),
    "mobility": (
        (1, "antimage_blink", "Blink"), (39, "queenofpain_blink", "Blink"),
        (17, "storm_spirit_ball_lightning", "Ball Lightning"), (145, "kez_grappling_claw", "Grappling Claw"),
        (41, "faceless_void_time_walk", "Time Walk"), (13, "puck_ethereal_jaunt", "Ethereal Jaunt"),
        (63, "weaver_shukuchi", "Shukuchi"), (106, "ember_spirit_activate_fire_remnant", "Activate Fire Remnant"),
    ),
    "vision": (
        (32, "riki_permanent_invisibility", "Cloak and Dagger"),
        (63, "weaver_shukuchi", "Shukuchi"),
        (62, "bounty_hunter_wind_walk", "Shadow Walk"), (88, "nyx_assassin_vendetta", "Vendetta"),
    ),
    "area": ((2, "axe_berserkers_call", "Berserker’s Call"), (33, "enigma_black_hole", "Black Hole"),
             (41, "faceless_void_chronosphere", "Chronosphere")),
    "physical_save": ((112, "winter_wyvern_cold_embrace", "Cold Embrace"),),
}

# Allied hero tools for jobs often covered by an item. Seeing a cast does not
# identify its target or prove the player personally benefited from it.
NATIVE = {
    "mobility": ((16, "sandking_burrowstrike", "Burrowstrike"), (26, "lion_voodoo", "Hex"),
                 (27, "shadow_shaman_voodoo", "Hex"), (49, "dragon_knight_dragon_tail", "Dragon Tail")),
    "vision": ((62, "bounty_hunter_track", "Track"), (28, "slardar_amplify_damage", "Corrosive Haze")),
    "healing": ((69, "doom_bringer_doom", "Doom"), (68, "ancient_apparition_ice_blast", "Ice Blast")),
}

# Close-range initiation tools for which acquiring Blink changes engagement range.
BLINK_SPELLS = {
    2: ("axe_berserkers_call", "Berserker’s Call"), 16: ("sandking_burrowstrike", "Burrowstrike"),
    26: ("lion_voodoo", "Hex"), 27: ("shadow_shaman_voodoo", "Hex"), 29: ("tidehunter_ravage", "Ravage"),
    96: ("centaur_hoof_stomp", "Hoof Stomp"), 97: ("magnataur_reverse_polarity", "Reverse Polarity"),
    104: ("legion_commander_duel", "Duel"), 28: ("slardar_slithereen_crush", "Slithereen Crush"),
}

# Personal defenses only cover their owner. Team tools are listed separately.
RULES = {
    "target": ("Направленный контроль", ("sphere", "black_king_bar"), (),
               "Linken’s Sphere может заблокировать одно направленное заклинание. BKB даёт окно защиты от перечисленного контроля при своевременной активации."),
    "pierce": ("Направленный контроль сквозь дебафф-иммунитет", ("sphere",), (),
               "Spellblock способен сорвать прямое применение в героя. Другая способность может заранее снять заряд. BKB не закрывает прямую цель от перечисленных ультимейтов."),
    "mobility": ("Удержать мобильную цель", ("abyssal_blade", "sheepstick"), ("abyssal_blade", "sheepstick"),
                 "Оглушение или Hex дают время атаковать до побега. Игроку с Abyssal нужен выход на дистанцию применения."),
    "vision": ("Раскрыть невидимость", ("dust", "ward_sentry", "gem"), ("dust", "ward_sentry", "gem"),
               "Учитывается обнаружение всей команды. Возможность раскрыть героя зависит от позиции и момента применения расходника."),
    "healing": ("Снизить восстановление здоровья", ("spirit_vessel", "skadi"), ("spirit_vessel", "skadi"),
                "Spirit Vessel снижает восстановление выбранной цели. Для эффекта Eye of Skadi по цели нужно атаковать её."),
    "evasion": ("Попадать сквозь уклонение Butterfly", ("monkey_king_bar",), (),
                "Если значимая часть урона героя идёт с атак, MKB даёт шанс игнорировать уклонение. Можно также распределить цели и положиться на урон способностями."),
    "area": ("Массовый контроль сквозь дебафф-иммунитет", (), (),
             "Проверяй расстояние до союзников и момент входа. Этот контроль может зацепить несколько героев и проходит сквозь BKB."),
    "physical_save": ("Смена цели под Cold Embrace", (), (),
                      "Winter Wyvern защищает союзника от физического урона и лечит его. Для героя, который бьёт с руки, здесь важны смена цели и магический урон союзников."),
}
RULE_ITEMS = {key for _, own, team, _ in RULES.values() for key in (*own, *team)}


def _inventory(player):
    slots = [_int(player.get(f"item_{i}")) for i in range(6)]
    keys = [ITEMS.get(str(item_id), f"ID {item_id}") for item_id in slots if item_id and item_id > 0]
    return keys, all(i is not None and i >= 0 for i in slots)


def _evidence(player, heroes):
    hero_id = _int(player.get("hero_id"))
    hero = heroes.get(hero_id, {}).get("localized_name") or f"Герой #{hero_id}"
    purchases = _purchases(player.get("purchase_log"))
    first = {}
    for p in purchases:
        first.setdefault(p["key"], p["time"])
    inventory, full_slots = _inventory(player)
    uses = player.get("item_uses")
    casts = {k for k, n in uses.items() if type(n) is int and n > 0} if isinstance(uses, dict) else set()
    full_log = isinstance(player.get("purchase_log"), list) and len(purchases) == len(player["purchase_log"])
    return {"slot": player["player_slot"], "id": hero_id, "hero": hero, "unit": heroes.get(hero_id, {}).get("name"),
            "first": first, "damage_taken": player.get("damage_taken"),
            "inventory": inventory, "slots_known": full_slots, "log_known": full_log,
            "items": set(inventory) | set(first) | casts, "healing": player.get("hero_healing"),
            "position": _int(player.get("position_est")), "worth": _int(player.get("net_worth"))}


def _item_name(key):
    record = ITEM_INFO.get(key)
    return record[0] if record else _item(key)


def _fights(raw):
    valid = []
    original = raw.get("teamfights")
    for f in original if isinstance(original, list) else []:
        if not isinstance(f, dict) or type(f.get("start")) is not int or type(f.get("end")) is not int:
            continue
        entries = f.get("players")
        if f["end"] >= f["start"] and isinstance(entries, list) and len(entries) == 10:
            valid.append(f)
    return sorted(valid, key=lambda f: f["start"])


def _fight_counts(fight, slot, key):
    part = fight["players"][SLOTS.index(slot)]
    counts = part.get(key) if isinstance(part, dict) else None
    if not isinstance(counts, dict) or any(type(n) is not int or n < 0 for n in counts.values()):
        return None
    return counts


def _item_evidence(key, selected, fights):
    purchase = selected["first"].get(key)
    after_count = known = after_known = 0
    intervals = []
    for f in fights:
        uses = _fight_counts(f, selected["slot"], "item_uses")
        if uses is None:
            continue
        known += 1
        after = purchase is not None and f["start"] > purchase
        after_known += int(after)
        n = uses.get(key, 0)
        if n:
            intervals.append({"start": f["start"], "end": f["end"], "casts": n, "after": after})
            if after:
                after_count += n
    return {"key": key, "purchase": purchase, "in_final": key in selected["inventory"],
            "fight_logs": known, "after_windows": after_known, "after_casts": after_count,
            "intervals": intervals}


def _threats(enemies, fights):
    result = {}
    for enemy in enemies:
        for category, abilities in SPELLS.items():
            for hero_id, key, name in abilities:
                if enemy["id"] != hero_id or not name:
                    continue
                times = {}
                for f in fights:
                    counts = _fight_counts(f, enemy["slot"], "ability_uses")
                    if counts and counts.get(key, 0) > 0:
                        times[f["start"]] = counts[key]
                result.setdefault(category, []).append({"hero": enemy["hero"], "name": name, "casts": times})
        for item in ("invis_sword", "silver_edge", "butterfly"):
            if item in enemy["items"]:
                category = "evasion" if item == "butterfly" else "vision"
                result.setdefault(category, []).append({"hero": enemy["hero"], "name": _item_name(item), "casts": {}})
        healing = enemy["healing"]
        if type(healing) in (float, int) and healing >= 1000:
            result.setdefault("healing", []).append({"hero": enemy["hero"], "name": f"лечение героев {int(healing)}", "casts": {}})
    return result


def extract_build(raw, slot, heroes):
    """Return JSON-serializable observations without guessing purchases from final slots."""
    if slot not in SLOTS:
        return {}
    raw_players = raw.get("players")
    if not isinstance(raw_players, list):
        return {}
    players = [_evidence(p, heroes) for p in raw_players if isinstance(p, dict) and p.get("player_slot") in SLOTS]
    picked = [p for p in players if p["slot"] == slot]
    if len(picked) != 1:
        return {}
    selected = picked[0]
    allies = [p for p in players if p["slot"] // 128 == slot // 128 and p["slot"] != slot]
    enemies = [p for p in players if p["slot"] // 128 != slot // 128]
    fights = _fights(raw)
    fight_items = set()
    for f in fights:
        uses = _fight_counts(f, slot, "item_uses") or {}
        fight_items.update(k for k, n in uses.items() if n > 0)
    selected["items"].update(fight_items)
    incoming = []
    received = selected.get("damage_taken")
    for p in enemies:
        unit = p["unit"]
        if (isinstance(received, dict) and isinstance(unit, str) and unit.startswith("npc_dota_hero_")
                and sum(other["unit"] == unit for other in players) == 1):
            dmg = received.get(unit)
            if type(dmg) is int and dmg > 0:
                incoming.append({"hero": p["hero"], "damage": dmg})
    incoming.sort(key=lambda entry: -entry["damage"])
    blink_combo = None
    if selected["id"] in BLINK_SPELLS and "blink" in selected["items"]:
        spell, label = BLINK_SPELLS[selected["id"]]
        windows = []
        for f in fights:
            item_uses = _fight_counts(f, slot, "item_uses") or {}
            spells = _fight_counts(f, slot, "ability_uses") or {}
            if item_uses.get("blink") and spells.get(spell):
                windows.append({"start": f["start"], "end": f["end"], "blinks": item_uses["blink"], "spells": spells[spell]})
        blink_combo = {"spell": label, "windows": windows, "purchase": selected["first"].get("blink")}
    key_items = (selected["items"] & (CORE_ITEMS | RULE_ITEMS)) | set(selected["inventory"])
    items = [_item_evidence(k, selected, fights) for k in sorted(key_items, key=lambda k: (selected["first"].get(k, float("inf")), k))]
    complete_draft = sorted(p["slot"] for p in players) == list(SLOTS) and all(p["id"] for p in players)
    findings = []
    for k, threats in _threats(enemies, fights).items():
        title, own_tools, ally_tools, note = RULES[k]
        personal = [key for key in own_tools if key in selected["items"]]
        team = [f'{p["hero"]} — {_item_name(key)}' for p in allies for key in ally_tools if key in p["items"]]
        native = []
        for p in [selected, *allies]:
            for hero_id, spell, label in NATIVE.get(k, ()):
                if p["id"] == hero_id:
                    casts = sum((_fight_counts(f, p["slot"], "ability_uses") or {}).get(spell, 0) for f in fights)
                    native.append(f'{p["hero"]} — {label}' + (f' (×{casts} в драках)' if casts else ' (по драфту)'))
        checks, timing = [], []
        for key in personal:
            purchase = selected["first"].get(key)
            if purchase is None:
                continue
            threat_windows = [f for f in fights if any(f["start"] in t["casts"] for t in threats)]
            if threat_windows:
                timing.append({"item": key, "purchase": purchase, "before": sum(f["end"] < purchase for f in threat_windows),
                               "overlap": sum(f["start"] <= purchase <= f["end"] for f in threat_windows),
                               "after": sum(f["start"] > purchase for f in threat_windows)})
            for f in fights:
                if f["start"] <= purchase:
                    continue
                threat_casts = [f'{t["name"]} ×{t["casts"][f["start"]]}' for t in threats if f["start"] in t["casts"]]
                if threat_casts:
                    uses = _fight_counts(f, slot, "item_uses")
                    checks.append({"item": key, "start": f["start"], "end": f["end"], "threats": threat_casts,
                                   "uses": uses.get(key, 0) if uses is not None else None})
                    break
        findings.append({"category": k, "title": title, "threats": threats, "personal": personal,
                         "team": team, "native": native, "checks": checks, "timing": timing, "note": note})
    return {"hero": selected["hero"], "allies": [p["hero"] for p in allies], "enemies": [p["hero"] for p in enemies],
            "draft_complete": complete_draft, "log_known": selected["log_known"], "slots_known": selected["slots_known"],
            "inventory": selected["inventory"], "items": items, "findings": findings, "fights": len(fights),
            "position": selected["position"], "worth": selected["worth"], "incoming": incoming, "blink_combo": blink_combo}


def render_build(data, *, patch_version=None):
    if not data:
        return "<p>Для разбора билда нужны драфт и карточка игрока. Обнови данные матча.</p>"
    lines = [f'<p><b>Своя команда</b> · {_h(", ".join([data["hero"], *data["allies"]]))}<br>'
             f'<b>Соперники</b> · {_h(", ".join(data["enemies"]) or "данные отсутствуют")}</p>']
    if not data["draft_complete"]:
        lines.append('<p class="meta">Состав указан частично. Разбор учитывает только известных героев.</p>')
    position, worth = data.get("position"), data.get("worth")
    if position in range(1, 6):
        economy = f' Ценность в конце игры — {worth:,} золота.'.replace(",", " ") if worth is not None else ''
        role_note = (" Для поддержки важно, может ли задачу взять союзник или более дешёвый расходник."
                     if position >= 4 else " Для героя основы важен баланс между фармом, уроном и временем появления защиты.")
        lines.append(f'<p class="meta">Оценка роли OpenDota — позиция {position}.{economy}{role_note}</p>')
    if data.get("incoming"):
        damage = [f'{p["hero"]} — {p["damage"]:,}'.replace(",", " ") for p in data["incoming"]]
        lines.append('<p><b>Полученный урон от героев драфта</b> · ' + _h(", ".join(damage))
                     + '. Показаны записи с именем самого вражеского героя в журнале полученного урона. '
                     'Призванные существа и другие отдельные юниты сюда не входят. Тип урона в этом срезе неизвестен.</p>')
    combo = data.get("blink_combo")
    if combo:
        bought = f' на {_clock(combo["purchase"])}' if combo["purchase"] is not None else ''
        lines.append(f'<div class="fact"><b>Вход в драку.</b> Blink Dagger{bought} позволяет подойти на дистанцию {_h(combo["spell"])}.</div>')
        if combo["windows"]:
            w = combo["windows"][0]
            lines.append(f'<p class="meta">Оба действия записаны в {len(combo["windows"])} окнах. '
                         f'Первое такое окно {_clock(w["start"])}–{_clock(w["end"])} · Blink ×{w["blinks"]} и '
                         f'{_h(combo["spell"])} ×{w["spells"]}. Очерёдность и цель из этих счётчиков неизвестны.</p>')
    tools = [f for f in data["findings"] if f["personal"]]
    if tools:
        lines.append('<p><b>Что закрывает билд.</b> ' + _h(" · ".join(f'{f["title"]} — {", ".join(_item_name(k) for k in f["personal"])}'
                                                                  for f in tools)) + '.</p>')
    for f in data["findings"]:
        threats = []
        for t in f["threats"]:
            n = sum(t["casts"].values())
            suffix = f" (×{n} в драках)" if n else ""
            threats.append(f'{t["hero"]} — {t["name"]}{suffix}')
        lines.append(f'<div class="fact"><p><b>{_h(f["title"])}</b> · {_h(", ".join(threats))}.</p>')
        if f["category"] in ("area", "physical_save"):
            lines.append('<p>Тактический риск. ' + _h(f["note"]) + '</p>')
        elif f["personal"]:
            lines.append('<p>У игрока есть подходящий инструмент. ' + _h(f["note"]) + '</p>')
        elif f["team"]:
            lines.append('<p>Ответ есть у союзников. ' + _h(f["note"]) + '</p>')
        elif f.get("native"):
            lines.append('<p>Эту задачу может взять на себя способность команды. ' + _h(f["note"]) + '</p>')
        else:
            lines.append('<p>Среди распознанных предметов ответ не виден. ' + _h(f["note"]) + '</p>')
            costs = [f'{_item_name(k)} — {ITEM_INFO[k][1]:,}'.replace(",", " ") for k in RULES[f["category"]][1]
                     if k in ITEM_INFO and type(ITEM_INFO[k][1]) is int and ITEM_INFO[k][1] > 0]
            if costs:
                lines.append('<small>Справочные цены в золоте · ' + _h(", ".join(costs))
                             + '. Доступный бюджет в момент угрозы по финальной ценности определить нельзя.</small>')
        if f["team"]:
            lines.append('<small>Подходящие командные предметы · ' + _h(", ".join(f["team"])) + '.</small>')
        if f.get("native"):
            lines.append('<small>Способности команды · ' + _h(", ".join(f["native"])) + '.</small>')
        for t in f.get("timing", []):
            timing = f'До покупки завершилось {t["before"]} драк с этой угрозой, после покупки началось {t["after"]}'
            if t["overlap"]:
                timing += f', покупка пришлась на окно ещё {t["overlap"]} драк'
            lines.append(f'<small>{_h(_item_name(t["item"]))} · {_clock(t["purchase"])}. {timing}.</small>')
        for check in f["checks"]:
            casts = check["uses"]
            use = f'применений {_item_name(check["item"])} у игрока — {casts}' if casts is not None else 'журнал предметов недоступен'
            lines.append(f'<small>{_clock(check["start"])}–{_clock(check["end"])} · {_h(", ".join(check["threats"]))}, {_h(use)}.</small>')
        lines.append('</div>')
    if not data["findings"]:
        lines.append('<p>Для этого драфта и доступных журналов в справочнике нет подходящей проверки.</p>')
    reference = 'Справочник механик сверен с описаниями Valve на 7.41f.'
    if patch_version and patch_version != "7.41f":
        reference += f' Матч сыгран на {_h(patch_version)}. Применимость каждой рекомендации к исторической версии требует сверки.'
    elif not patch_version:
        reference += ' Подпатч этой карты в данном разделе не подтверждён.'
    lines.append('<p class="meta">' + reference + ' Время в строке — интервал драки. '
                 'Совпадение предмета и заклинания в одной драке оставляет цель, порядок и исход взаимодействия неизвестными. '
                 'Способности союзников, позиция, фарм и стоимость альтернатив тоже влияют на выбор.</p>')
    lines.append('<h3>Тайминги билда</h3>')
    if not data["log_known"]:
        lines.append('<p class="meta">Журнал покупок неполон. Время показывается только у найденных записей.</p>')
    if data["items"]:
        lines.append('<div class="scroll"><table><thead><tr><th>Основной предмет</th><th>Первая запись покупки</th>'
                     '<th>Активации во всех драках с журналом</th><th>Из них после покупки¹</th><th>Первое окно после покупки с активацией</th></tr></thead><tbody>')
        for item in data["items"]:
            uses = str(item["after_casts"]) if item["after_windows"] else "—"
            example = next((i for i in item["intervals"] if i["after"]), None)
            window = f'{_clock(example["start"])}–{_clock(example["end"])}' if example else "—"
            total = sum(i["casts"] for i in item["intervals"]) if item["fight_logs"] else "—"
            lines.append(f'<tr><td>{_h(_item_name(item["key"]))}</td><td>{_clock(item["purchase"])}</td><td>{total}</td><td>{uses}</td><td>{window}</td></tr>')
        lines.append('</tbody></table></div>')
    final = ", ".join(_item_name(key) for key in data["inventory"])
    lines.append('<p><b>Шесть слотов в конце игры</b> · ' + _h(final or ("пусто" if data["slots_known"] else "нет сведений"))
                 + ('' if data['slots_known'] else ' (данные неполны)') + '.</p>')
    lines.append('<p class="meta">¹ В колонке «после покупки» учитываются окна с доступным журналом, начавшиеся строго после покупки. '
                 'Прочерк означает, что подходящих данных нет. Пассивные эффекты учитываются отдельно от применения кнопки. '
                 'У Linken’s Sphere счётчик относится к передаче защиты, количество пассивных блоков неизвестно. '
                 'Лечение противника учитывается от 1 000 ед. за карту. '
                 '<a href="https://www.dota2.com/datafeed/itemdata?language=english&amp;item_id=123">Описание Spellblock у Valve</a>.</p>')
    return "".join(lines)
