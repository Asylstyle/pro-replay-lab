"""Evidence-bound replay coaching. Fight counters have no per-cast timestamps."""

from .analysis import select_player
from .build_review import ITEM_CATALOG, ITEM_INFO, SPELLS, _evidence, _fight_counts, _fights, _item_name
from .fight_review import PREFIX, SLOTS, _counts, _spell_name, extract_fights
from .report import _clock


# Valve's hero and item datafeeds expose the names, damage types, dispels and
# target rules used below. Each recommendation retains its item source.
EXTRA_SPELLS = {
    "target": ((84, "ogre_magi_fireblast", "Fireblast"), (84, "ogre_magi_unrefined_fireblast", "Unrefined Fireblast"),
               (81, "chaos_knight_chaos_bolt", "Chaos Bolt")),
    "pierce": ((14, "pudge_dismember", "Dismember"),),
    "magic_burst": ((22, "zuus_lightning_bolt", "Lightning Bolt"), (22, "zuus_arc_lightning", "Arc Lightning"),
                    (22, "zuus_thundergods_wrath", "Thundergod’s Wrath"), (25, "lina_dragon_slave", "Dragon Slave"),
                    (25, "lina_light_strike_array", "Light Strike Array"), (26, "lion_finger_of_death", "Finger of Death"),
                    (5, "crystal_maiden_crystal_nova", "Crystal Nova"), (39, "queenofpain_scream_of_pain", "Scream of Pain"),
                    (11, "nevermore_requiem", "Requiem of Souls"), (52, "leshrac_lightning_storm", "Lightning Storm"),
                    (101, "skywrath_mage_arcane_bolt", "Arcane Bolt"), (45, "pugna_nether_blast", "Nether Blast")),
    "silence": ((13, "puck_waning_rift", "Waning Rift"), (43, "death_prophet_silence", "Silence"),
                (101, "skywrath_mage_ancient_seal", "Ancient Seal"), (6, "drow_ranger_wave_of_silence", "Gust"),
                (75, "silencer_global_silence", "Global Silence")),
    "physical_pressure": ((18, "sven_gods_strength", "God’s Strength"), (70, "ursa_overpower", "Overpower"),
                          (8, "juggernaut_omni_slash", "Omnislash"), (8, "juggernaut_swift_slash", "Swiftslash")),
    "area_damage": ((101, "skywrath_mage_mystic_flare", "Mystic Flare"),
                    (5, "crystal_maiden_freezing_field", "Freezing Field")),
}
CANDIDATES = {
    "target": ("black_king_bar", "sphere", "glimmer_cape"), "pierce": ("sphere",),
    "mobility": ("sheepstick", "abyssal_blade"), "vision": ("dust", "ward_sentry"),
    "magic_burst": ("glimmer_cape", "pipe", "black_king_bar"),
    "silence": ("manta", "cyclone", "black_king_bar"),
    "physical_pressure": ("ghost", "force_staff"),
    "area_damage": ("force_staff", "glimmer_cape", "pipe", "black_king_bar"),
    "area": (), "physical_save": (),
}
SPELL_LOOKUP = {}
for table in (SPELLS, EXTRA_SPELLS):
    for category, spells in table.items():
        for owner, key, label in spells:
            SPELL_LOOKUP.setdefault(key, []).append((category, label, owner))
ITEM_THREATS = {"orchid": "silence", "bloodthorn": "silence", "invis_sword": "vision", "silver_edge": "vision"}
ITEM_IDS = {entry[0]: item_id for item_id, entry in ITEM_CATALOG.items()}
FAMILIES = {"force_staff": ("force_staff", "hurricane_pike"), "cyclone": ("cyclone", "wind_waker"),
            "ghost": ("ghost", "ethereal_blade")}
BUY_ROLES = {"sphere": (1, 2, 3), "black_king_bar": (1, 2, 3), "manta": (1, 2),
             "abyssal_blade": (1, 3), "sheepstick": (2, 3, 4), "pipe": (3, 4),
             "glimmer_cape": (4, 5), "ghost": (2, 4, 5), "force_staff": (2, 3, 4, 5)}
ITEM_USE = {
    "black_king_bar": "Включить перед входом под обычный контроль или магический урон. Нужны готовая кнопка и время нажать её.",
    "sphere": "Сохранить готовый Spellblock для прямого применения в тебя или передать защиту союзнику. Более дешёвый направленный каст может снять заряд.",
    "sheepstick": "Дать Hex с видимости до ухода мобильной цели. Сверь дистанцию и наличие дебафф-иммунитета.",
    "abyssal_blade": "Оглушить мобильную цель до прыжка. Для активации к ней нужно подойти вплотную.",
    "ward_sentry": "Заранее поставить Sentry в зоне будущей драки и обеспечить союзный обзор этой области.",
    "dust": "Раскрыть невидимость в зоне драки. Проверь дистанцию до врага и кто из союзников уже принёс обнаружение.",
    "glimmer_cape": "Заранее дать магический барьер союзнику под фокусом или себе. У эффекта есть задержка, истинное зрение раскрывает невидимость.",
    "pipe": "Включить командный магический барьер до залпа, когда союзники находятся в радиусе.",
    "manta": "Снять развеиваемое безмолвие с себя активацией Manta. Проверь, позволяли ли другие эффекты нажать предмет.",
    "cyclone": "Применить Cyclone на себя для базового развеивания. Учитывай время в воздухе и положение врагов при приземлении.",
    "ghost": "Сменить форму на время опасных физических атак. В этой форме нельзя атаковать и возрастает уязвимость к магии.",
    "force_staff": "Сдвинуть себя или союзника в направлении его взгляда, увеличив дистанцию до опасной области или атакующего.",
}
TACTIC = {
    "target": "смени угол входа, чтобы первым применить свой контроль, или дождись, когда противник потратит направленное заклинание",
    "pierce": "подходи с другого угла и оставь союзнику возможность защищать цель с дистанции",
    "mobility": "согласуй первый контроль с союзником и оставь продолжение на попытку побега",
    "vision": "до преследования обеспечь обнаружение на пути отхода и проверь видимость врага",
    "area": "разойдись с ближайшим союзником до входа, чтобы массовый контроль не накрыл обоих",
    "physical_save": "переключи физические атаки с героя под Cold Embrace на доступного противника",
    "magic_burst": "проверь возможность переждать магический залп в тумане или прикрыть его барьером до урона",
    "silence": "сохрани доступное развеивание до безмолвия или отложи вход, пока источник безмолвия виден",
    "physical_pressure": "разорви дистанцию на время усиленных атак и выбери точку для повторного входа",
    "area_damage": "найди кратчайший безопасный выход из области и оставь союзнику путь к краю",
}
ROLE = {
    1: ("Посмотри с позиции керри, когда можно было начать безопасно бить доступную цель.",
        "Отметь первый безопасный удар и секунду, когда пришлось прекратить атаковать."),
    2: ("Посмотри с позиции мидера, откуда можно было добраться до уязвимой цели и куда отойти после своего прокаста.",
        "Отметь позицию до первого каста и доступный маршрут отхода после него."),
    3: ("Посмотри с позиции офлейнера, успевали ли союзники продолжить твой вход.",
        "Сопоставь момент входа с позицией ближайшего союзника и его первым действием."),
    4: ("Посмотри с четвёртой позиции, откуда можно было продолжить контроль союзника или прервать ответ врага.",
        "Отметь первую доступную цель и момент, когда союзный контроль требовал продолжения."),
    5: ("Посмотри с пятой позиции, какого союзника можно было прикрыть с безопасной дистанции.",
        "Отметь момент, когда союзнику понадобилась помощь, и проверь свой радиус применения и кулдауны."),
}
GENERIC_ROLE = ("Открой повтор с обзором своей команды.", "Сверь видимость цели, дистанцию и готовность своих действий.")
SPECIFIC_TACTIC = {
    "legion_commander_duel": "задержи выход на дистанцию Duel, пока Legion Commander не покажется или союзник не займёт позицию для прикрытия цели",
    "winter_wyvern_winters_curse": "оставь пространство между собой и ближайшим союзником, чтобы Winter’s Curse не связала вас обоих",
    "bane_fiends_grip": "сохрани у команды прерывание канала с другого угла и проверь, кто видел Bane",
    "pudge_dismember": "держи союзника с прерыванием вне досягаемости Pudge и в пределах помощи цели Dismember",
    "shadow_shaman_shackles": "обозначь союзника, способного прервать канал Shackles, и проверь его дальность до Shadow Shaman",
}


def _active_alternative(item, threat):
    """A previously purchased button with zero observed uses is a replay question."""
    key, ability = item["key"], threat["ability"]
    if key == "black_king_bar":
        if threat["category"] == "target":
            return (f"Если BKB была в активном слоте и готова, вариант — включить её до выхода "
                    f"на дистанцию {ability}, пока ещё можно было нажать предмет.")
        return (f"Если BKB была готова и тебе угрожал магический урон от {ability}, "
                "проверь вход с активацией до попадания заклинания.")
    if key == "glimmer_cape":
        if threat["category"] == "target":
            return (f"Если союзник попадал на дистанцию {ability}, можно было заранее дать ему Glimmer Cape. "
                    "Проверь дальность, задержку невидимости и обнаружение у врага.")
        return (f"Если тебе или союзнику угрожал урон от {ability}, а Glimmer Cape был готов, "
                "вариант — заранее дать барьер герою под ударом. Проверь, успевал ли барьер появиться.")
    if key in ("manta", "cyclone"):
        return (f"Если безмолвие от {ability} пришлось на тебя и предмет {item['name']} был готов, "
                "проверь, можно ли было снять эффект с себя до следующего заклинания.")
    if key == "force_staff":
        return (f"Если союзнику угрожал эффект от {ability} и Force Staff был готов, проверь, "
                "можно ли было вытолкнуть союзника в безопасную сторону с учётом направления его взгляда.")
    if key == "ghost":
        return (f"Если противник мог атаковать тебя под {ability} и Ghost Scepter был готов, "
                "проверь переход в призрачную форму до первой серии физических атак.")
    if key in ("sheepstick", "abyssal_blade"):
        return (f"Если герой {threat['hero']} был виден в радиусе предмета, проверь, "
                f"мог ли {item['name']} дать контроль до ухода через {ability}.")
    if key in ("dust", "ward_sentry"):
        return (f"Если перед {ability} у тебя оставался заряд {item['name']}, "
                "проверь, можно ли было накрыть обнаружением подход к драке до ухода врага в невидимость.")
    if key == "pipe":
        return (f"Если союзники попадали под {ability} и были рядом, проверь, "
                "позволяла ли готовая Pipe of Insight дать им барьер до залпа.")
    return None


def _team_events(players, units, slot, start, end):
    """Recover deaths from the victim's own log or an enemy's timed kill log."""
    events = {}
    for p in players:
        s = p["player_slot"]
        for field, tag in (("deaths_log", "death"), ("kills_log", "kill"), ("buyback_log", "buyback")):
            log = p.get(field)
            if not isinstance(log, list):
                continue
            for entry in log:
                if not isinstance(entry, dict):
                    continue
                time, key = entry.get("time"), entry.get("key")
                if type(time) is not int or not start <= time <= end:
                    continue
                target = units.get(key) if isinstance(key, str) else None
                if tag == "kill" and target and s // 128 != slot // 128 and target[0] // 128 == slot // 128:
                    victim = target[0]
                elif tag == "death" and s // 128 == slot // 128:
                    victim = s
                else:
                    victim = None
                if victim is not None:
                    kind = "death" if victim == slot else "ally_death"
                    name = next((label for a, label in units.values() if a == victim), "союзника")
                    label = "Твоя смерть" if victim == slot else f"Смерть союзника {name}"
                    events[time, kind, victim] = {"time": time, "kind": kind, "text": label}
                elif s == slot and tag == "kill" and isinstance(key, str) and key.startswith(PREFIX):
                    name = target[1] if target else _spell_name(key.removeprefix(PREFIX), "")
                    events[time, tag, key] = {"time": time, "kind": tag, "text": f"Твоё убийство — {name}"}
                elif s == slot and tag == "buyback":
                    events[time, tag, s] = {"time": time, "kind": tag, "text": "Твой выкуп"}
    return sorted(events.values(), key=lambda e: e["time"])


def _threats(window, enemies, unit):
    result = []
    for enemy in enemies:
        slot = enemy["slot"]
        actor = window["players"][SLOTS.index(slot)]
        if not isinstance(actor, dict):
            continue
        targets = actor.get("ability_targets")
        for source in ("ability_uses", "item_uses"):
            for key, n in (_fight_counts(window, slot, source) or {}).items():
                if n <= 0:
                    continue
                if source == "ability_uses":
                    definitions = SPELL_LOOKUP.get(key, ())
                else:
                    definitions = [(ITEM_THREATS[key], _item_name(key), None)] if key in ITEM_THREATS else ()
                for category, label, owner in definitions:
                    on_player = None
                    if source == "ability_uses" and isinstance(targets, dict) and unit:
                        on_player = (_counts(targets.get(key)) or {}).get(unit)
                    result.append({"category": category, "key": key, "ability": label, "hero": enemy["hero"],
                                   "hero_id": enemy["id"], "casts": n, "on_player": on_player,
                                   "source": source, "definition_hero_id": owner})
    priority = ("area", "pierce", "target", "silence", "vision", "physical_pressure", "area_damage",
                "magic_burst", "mobility", "physical_save")
    return sorted(result, key=lambda t: (not t["on_player"], priority.index(t["category"]), -t["casts"], t["key"]))


def _item_state(item, selected, window, uses):
    family = FAMILIES.get(item, (item,))
    start, end = window["start"], window["end"]
    if item == "ward_sentry" and selected["sentries"] is not None:
        placed = [t for t in selected["sentries"] if start <= t <= end]
        if placed:
            return "used", "Установка Sentry Ward записана на " + ", ".join(_clock(t) for t in placed) + "."
    used = [(k, uses[k]) for k in family if uses and uses.get(k, 0) > 0]
    bought = [(selected["first"][k], k) for k in family if k in selected["first"]]
    previous = [(finish, k) for k in family if (finish := selected["first_use_end"].get(k)) is not None and finish < start]
    if used:
        key, count = used[-1]
        label = f"{_item_name(key)} ×{count} по журналу этой драки."
        if item == "sphere":
            label += " Это передача защиты. Пассивные блоки здесь не подсчитаны."
        return "used", label
    if bought:
        when, key = min(bought)
        label = f"Первая запись покупки {_item_name(key)} — {_clock(when)}."
        if when < start:
            if item == "sphere":
                label += " Готовность пассивного блока проверяется в повторе."
            elif item == "ward_sentry":
                label += (" В этом интервале журнал установок Sentry пуст." if selected["sentries"] is not None else
                          " Точный журнал установок Sentry недоступен.")
                label += " Проверь оставшиеся заряды и обзор уже установленного варда."
            elif item == "dust":
                label += " Это расходник. Проверь, оставался ли заряд перед дракой."
                if uses is not None:
                    label += " Применения Dust в этом окне не записаны."
            elif uses is None:
                label += " Журнал применений в этой драке отсутствует."
            else:
                label += " В журнале драки активация отсутствует. Проверь доставку, кулдаун и возможность нажать."
            return "prior_purchase", label
        if previous:
            finish, used_key = min(previous)
            label += f" Однако {_item_name(used_key)} уже применялся в предыдущей драке до {_clock(finish)}."
            return "unknown_timing", label
        return ("during_purchase" if when <= end else "later_purchase"), label
    if previous:
        finish, key = min(previous)
        return "unknown_timing", (f"{_item_name(key)} уже применялся в более ранней драке, закончившейся на {_clock(finish)}. "
                                  "Проверь в повторе, был ли предмет по-прежнему доступен.")
    if any(k in selected["items"] for k in family):
        return "unknown_timing", "Предмет или его улучшение встречается в итоговых данных. Момент получения неизвестен."
    if not selected["log_known"]:
        return "unknown_timing", "Журнал покупок неполон. Проверь по повтору, был ли уже такой предмет."
    return "unseen", "В доступном журнале покупок предмет и его улучшения не встречаются."


def _items(threats, selected, window, allies, position):
    result, seen = [], set()
    uses = _fight_counts(window, selected["slot"], "item_uses")
    for threat in threats:
        for key in CANDIDATES[threat["category"]]:
            if key in seen:
                continue
            seen.add(key)
            status, evidence = _item_state(key, selected, window, uses)
            role_ok = position is None or position in BUY_ROLES.get(key, range(1, 6))
            if not role_ok and status == "unseen":
                continue
            cost = ITEM_INFO[key][1]
            if status in ("unseen", "later_purchase"):
                if cost >= 1500 and window["start"] < 600:
                    evidence += " Рассмотри как план следующего слота после линии."
                if cost >= 1500:
                    evidence += " Доступное золото и срок доставки нужно сверить в повторе."
            if key in ("dust", "ward_sentry", "pipe"):
                team_uses = [a["hero"] for a in allies if (_fight_counts(window, a["slot"], "item_uses") or {}).get(key)
                             or key == "ward_sentry" and any(window["start"] <= t <= window["end"] for t in a["sentries"] or ())]
                if team_uses:
                    evidence += " Этот предмет уже применяли в том же интервале союзники — " + ", ".join(team_uses) + "."
            rationale = ITEM_USE[key]
            if key == "glimmer_cape" and threat["category"] == "target":
                rationale = ("Если противнику нужна видимая цель, заранее прикрой союзника Glimmer. "
                             "Проверь задержку невидимости и обнаружение у врага. Уже наложенный контроль сохранится.")
            result.append({"key": key, "name": _item_name(key), "category": threat["category"], "status": status,
                           "rationale": rationale, "evidence": evidence, "cost": cost,
                           "source_url": "https://www.dota2.com/datafeed/itemdata?language=english&item_id=" + ITEM_IDS[key],
                           "hero_id": threat["hero_id"], "ability": threat["ability"], "threat_hero": threat["hero"]})
    return result


def analyze_fights(raw, slot, heroes, *, position=None):
    """Return chronological, auditable advice for windows with personal evidence."""
    player = select_player(raw, None, slot)
    if player is None or type(slot) is not int or slot not in SLOTS:
        return []
    estimates = extract_fights(raw, slot, heroes)
    windows = {(w["start"], w["end"]): w for w in _fights(raw)}
    roster = [p for p in raw["players"] if isinstance(p, dict) and type(p.get("player_slot")) is int and p["player_slot"] in SLOTS]
    if len({p["player_slot"] for p in roster}) != len(roster):
        return []
    members = [_evidence(p, heroes) for p in roster]
    for p, member in zip(roster, members):
        wards = p.get("sen_log")
        member["sentries"] = ([w["time"] for w in wards if isinstance(w, dict) and type(w.get("time")) is int
                               and not w.get("entityleft")] if isinstance(wards, list) else None)
    selected = next(p for p in members if p["slot"] == slot)
    selected["first_use_end"] = {}
    for w in sorted(windows.values(), key=lambda interval: interval["end"]):
        for key, n in (_fight_counts(w, slot, "item_uses") or {}).items():
            if n > 0:
                selected["first_use_end"].setdefault(key, w["end"])
    selected["items"].update(selected["first_use_end"])
    enemies = [p for p in members if p["slot"] // 128 != slot // 128]
    allies = [p for p in members if p["slot"] != slot and p["slot"] // 128 == slot // 128]
    units = {p["unit"]: (p["slot"], p["hero"]) for p in members if p["unit"] and
             sum(q["unit"] == p["unit"] for q in members) == 1}
    position = position if position is not None else selected["position"]
    position = position if type(position) is int and position in range(1, 6) else None
    role_action, check = ROLE.get(position, GENERIC_ROLE)
    results = []
    for fight in estimates["intervals"]:
        start, end = fight["start"], fight["end"]
        window = windows[start, end]
        events = _team_events(roster, units, slot, start, end)
        involved = any((fight[k] or 0) > 0 for k in ("deaths", "buybacks", "damage", "healing"))
        if not (involved or fight["abilities"] or fight["items"] or fight["kills"] or
                any(e["kind"] != "ally_death" for e in events)):
            continue
        anchor = next((e for kind in ("death", "kill", "buyback", "ally_death")
                       for e in events if e["kind"] == kind),
                      {"kind": "interval", "time": start, "text": "Начало интервала OpenDota"})
        threats = _threats(window, enemies, selected["unit"])
        items = _items(threats, selected, window, allies, position)
        observations = []
        if fight["abilities"]:
            key, count = max(fight["abilities"].items(), key=lambda p: p[1])
            name = _spell_name(key, estimates["hero_prefix"])
            observations.append(f"Твои применения {name} за драку — {count}.")
        if fight["items"]:
            key, count = max(fight["items"].items(), key=lambda p: p[1])
            observations.append(f"Твои применения {_item_name(key)} за драку — {count}.")
        if fight["damage"] is not None:
            observations.append(f"Твой урон героям за интервал — {fight['damage']}.")
        if fight["healing"]:
            observations.append(f"Лечение за интервал — {fight['healing']}.")
        if fight["deaths"]:
            observations.append(f"Твои смерти за интервал — {fight['deaths']}.")
        action = role_action
        if threats:
            t = threats[0]
            sentence = f"У {t['hero']} за интервал записано {t['ability']} ×{t['casts']}."
            if t["on_player"]:
                sentence += f" Твой герой отмечен целью {t['on_player']} раз."
            observations.insert(0, sentence)
            tactic = SPECIFIC_TACTIC.get(t["key"], TACTIC[t["category"]])
            relevant = [i for i in items if i["hero_id"] == t["hero_id"] and i["ability"] == t["ability"]]
            held = next((i for i in relevant if i["status"] == "prior_purchase" and i["key"] != "sphere"), None)
            measured = (selected["sentries"] is not None if held and held["key"] == "ward_sentry"
                        else fight["items"] is not None)
            alternative = _active_alternative(held, t) if held and measured else None
            if alternative:
                action += " " + alternative
                check_item = (f" Сверь доставку и кулдаун {held['name']} на момент {t['ability']}. "
                              "Учитывай другие заклинания, под которые стоило сохранить предмет.")
            else:
                later = next((i for i in relevant if i["status"] == "later_purchase"), None)
                if later and all(i["status"] in ("later_purchase", "unseen") for i in relevant):
                    if t["category"] == "magic_burst":
                        tactic = "проверь более поздний выход из тумана, когда противник уже потратил залп"
                    elif t["category"] == "silence":
                        tactic = "проверь вход после безмолвия, сохранив возможность применить свою способность"
                    action += f" Первая запись покупки {later['name']} появилась после этой драки."
                    check_item = (f" Проверь отдельно, позволяли ли золото и доставка собрать {later['name']} "
                                  f"раньше ради ответа на {t['ability']}.")
                else:
                    check_item = " Найди сам каст в повторе и сверь, где стояла его цель."
                action += f" Против {t['ability']} ({t['hero']}) {tactic}."
        else:
            action += " Сравни вход в этот момент с коротким ожиданием ближайшего союзника."
            check_item = ""
        results.append({"start": start, "end": end, "anchor": anchor["time"], "anchor_kind": anchor["kind"],
                        "anchor_text": anchor["text"], "events": events, "position": position, "observations": observations,
                        "action": action, "check": check + check_item, "threats": threats, "items": items})
    return sorted(results, key=lambda f: (f["start"], f["end"]))
