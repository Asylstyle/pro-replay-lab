"""Check a counterfactual order against a single, complete Source 2 sample.

An available order is not proof the play would succeed. In particular, ranges
to unit targets, ground-target spells, and self-centered AoEs are different.
Never combine a ready button in one frame with vision or reach in another.
"""

from bisect import bisect_left, bisect_right
from collections import defaultdict
import json
import math
from pathlib import Path

from .decision_actions import ACTIVE_ITEMS, PIERCES_IMMUNITY, SELF_DEFENSE


CASTS = json.loads(Path(__file__).with_name("replay_casts.json").read_text(encoding="utf8"))
TARGETED = frozenset(CASTS["buttons"])
SELF = set(SELF_DEFENSE) | {"item_black_king_bar", "item_ghost", "item_manta", "item_cyclone",
                           "item_force_staff", "item_glimmer_cape", "item_pipe"}
GLOBAL = {"chen_hand_of_god", "silencer_global_silence"}
SLOTS = {*range(5), *range(128, 133)}
# MODIFIER_STATE indices from Valve's client/modifierstate.h on 7.41f.
DISABLED = (5, 6, 11, 19, 20, 33, 47, 48)
LABELS = {"actor": "Герой игрока", "target": "Цель действия", "button": "Кнопка",
          "cooldown": "Перезарядка", "mana": "Мана", "control": "Свобода действий",
          "vision": "Обзор команды", "range": "Дистанция", "channel": "Вражеский канал"}


def _num(value):
    return type(value) in (int, float) and math.isfinite(value)


def _check(key, yes, detail):
    return {"key": key, "label": LABELS[key], "state": {True: "yes", False: "no", None: "unknown"}[yes],
            "detail": detail}


def _bits(unit, indices):
    mask = unit.get("unit_state")
    if type(mask) is not int or not 0 <= mask < 1 << 64:
        return None
    return any(mask & (1 << i) for i in indices)


def _alive(hero):
    value = hero.get("alive")
    return value if type(value) is bool else None


def _button(actor, key):
    buttons = actor.get("buttons")
    if not isinstance(buttons, dict):
        return {}
    b = buttons.get(key, buttons.get(key.removeprefix("item_")))
    return b if isinstance(b, dict) else {}


def _ready(actor, button, is_item):
    known = True if button else None
    detail = "Запись кнопки в этом кадре отсутствует."
    if button:
        lvl = button.get("level")
        slot = button.get("slot")
        equipped = 0 <= slot < 6 if type(slot) is int else None
        learned = lvl > 0 if type(lvl) is int else None
        active = button.get("active")
        active = active if type(active) is bool else None
        states = [equipped if is_item else learned, active if type(active) is bool else None]
        if button.get("in_phase") is True or button.get("channeling") is True:
            states.append(False)
            detail = "Герой уже применяет эту кнопку."
        elif is_item:
            detail = (f"Слот {slot + 1}. " if type(slot) is int and slot >= 0 else "") + {
                True: "Предмет активен.", False: "Предмет недоступен.", None: "Активность неизвестна."}.get(active)
        else:
            detail = (f"Уровень {lvl}. " if type(lvl) is int else "") + {
                True: "Способность активна.", False: "Способность отключена.", None: "Активность неизвестна."}.get(active)
        known = False if False in states else None if None in states else True
    cd = button.get("cooldown")
    cd_ok = cd <= 0 if _num(cd) else None
    cd_detail = (f"Осталось {max(0, cd):.1f} сек." if _num(cd) else "Значение перезарядки отсутствует.")
    if button.get("indefinite") is True or button.get("frozen") is True or button.get("charges") == 0:
        cd_ok, cd_detail = False, "Кнопка заблокирована, её восстановление остановлено или закончились заряды."
    cost, mana = button.get("mana_cost"), actor.get("mana")
    mana_ok = mana >= cost if _num(mana) and _num(cost) and mana >= 0 and cost >= 0 else None
    mana_detail = (f"У героя {mana:.0f} маны, нужно {cost:.0f}." if mana_ok is not None
                   else "Нет значения текущей маны или стоимости.")
    return [_check("button", known, detail), _check("cooldown", cd_ok, cd_detail),
            _check("mana", mana_ok, mana_detail)]


def _visibility(enemy, slot):
    team = "2" if slot < 128 else "3"
    visible = enemy.get("visible_to")
    flag = visible.get(team) if isinstance(visible, dict) else None
    if type(flag) is not bool:
        return _check("vision", None, "Нет бита командного обзора для этого врага.")
    return _check("vision", flag, "Враг был виден команде." if flag else "Враг был скрыт от команды.")


def _control(actor, is_item, enemy_target):
    flags = DISABLED + ((4,) if is_item else (3,)) + ((15,) if enemy_target else ())
    blocked = _bits(actor, flags)
    if blocked:
        return _check("control", False, "На герое отмечен запрет действия или контроль.")
    if blocked is None:
        return _check("control", None, "У повтора нет состояния контроля героя.")
    if any(isinstance(b, dict) and (b.get("channeling") is True or b.get("in_phase") is True)
           for b in (actor.get("buttons") or {}).values()):
        return _check("control", None, "Герой уже применял другую способность. Нужно проверить возможность прерывания.")
    return _check("control", True, "По состоянию героя запрета на это нажатие нет.")


def _recipient(target, side, key):
    alive = _alive(target)
    if alive is False:
        return _check("target", False, "Цель уже погибла.")
    flags = (8, 33, 37, {"self": 40, "ally": 38, "enemy": 39}[side])
    if side == "enemy" and key not in PIERCES_IMMUNITY and key != "item_abyssal_blade":
        flags += (9, 56)
    unsuitable = _bits(target, flags)
    if unsuitable:
        return _check("target", False, "У цели отмечена защита или недоступность для этого действия.")
    if alive is None or unsuitable is None:
        return _check("target", None, "Жизнь или состояние цели не установлены.")
    return _check("target", True, "Цель жива, проверенные запреты отсутствуют.")


def _distance(actor, target):
    coords = (actor.get("x"), actor.get("y"), target.get("x"), target.get("y"))
    if all(_num(c) for c in coords):
        return math.hypot(coords[0] - coords[2], coords[1] - coords[3])
    return None


def _reach(key, button, actor, target, side, patch_known, clean_draft):
    if side == "self" and key in SELF:
        return _check("range", True, "Применяется на себя.")
    if key in GLOBAL and patch_known:
        return _check("range", True, "Область действия глобальная.")
    distance = _distance(actor, target)
    if distance is None:
        return _check("range", None, "Координаты одного из героев отсутствуют.")
    prefix = f"Между героями {distance:.0f} игровых единиц. "
    spec = CASTS["buttons"].get(key)
    if not spec or side not in spec["sides"]:
        return _check("range", None, prefix + "Геометрия действия на эту цель не проверена.")
    effective = button.get("cast_range")
    if _num(effective) and effective > 0:
        if distance <= effective:
            return _check("range", True, prefix + f"Дальность кнопки {effective:g}.")
        # Unit target reach may include hull radii. Don't reject a marginal difference.
        hulls = (actor.get("hull_radius"), target.get("hull_radius"))
        proven_outside = all(_num(r) and r >= 0 for r in hulls) and distance > effective + sum(hulls)
        return _check("range", False if proven_outside else None,
                      prefix + f"Дальность кнопки {effective:g}, положение на границе учитывает размеры героев.")
    if not patch_known:
        return _check("range", None, prefix + "Нет подтверждённой дальности для версии матча.")
    base = spec["range"]
    if clean_draft and distance <= base:
        return _check("range", True, prefix + f"В пределах базовой дальности {base}, даже без бонусов.")
    return _check("range", None, prefix + f"Базовая дальность {base}. Эффективная дальность в этом кадре неизвестна.")


def _unknown(reason):
    return {"status": "unknown", "time": None, "tick": None, "checks": [], "reason": reason}


class ReplayVerifier:
    """Index once per match. A lazy frame_window(lo, hi) may replace the frame list."""

    def __init__(self, timeline, *, match_id, start_time=None, hero_ids=None, game_mode=None, patch_version=None):
        timeline = timeline if isinstance(timeline, dict) else {}
        self.valid = (timeline.get("format") == "pro-replay-truth-v1" and type(match_id) is int and
                      type(timeline.get("match_id")) is int and match_id > 0 and timeline["match_id"] == match_id)
        self.patch_known = (patch_version == CASTS["patch"] if patch_version
                            else type(start_time) is int and CASTS["from"] <= start_time <= CASTS["through"])
        # Bane's Enfeeble can reduce even the base value. Ability Draft and
        # custom rosters can grant this spell independently of the ten heroes.
        self.clean_draft = (game_mode in (1, 2, 3, 4, 22, 23) and isinstance(hero_ids, (list, tuple)) and
                            len(hero_ids) == 10 and all(type(n) is int and n > 0 for n in hero_ids)
                            and len(set(hero_ids)) == 10 and 3 not in hero_ids)
        self.window = timeline.get("frame_window")
        frames = timeline.get("frames", [])
        self.frames = sorted((f for f in frames if isinstance(f, dict) and type(f.get("time")) is int and
                              type(f.get("tick")) is int), key=lambda f: (f["time"], f["tick"])) if isinstance(frames, list) else []
        self.times = [f["time"] for f in self.frames]
        self.deaths = defaultdict(list)
        self.casts = defaultdict(list)
        for event in timeline.get("events") or ():
            if (not isinstance(event, dict) or type(event.get("time")) is not int or
                    type(event.get("tick")) is not int or not _num(event.get("game_time"))):
                continue
            kind, actor, target = (event.get(s) for s in ("kind", "actor_slot", "target_slot"))
            if kind == "death":
                self.deaths[(actor, target)].append(event)
            if kind == "cast" and isinstance(event.get("button_key"), str):
                self.casts[(actor, event["button_key"].removeprefix("item_"))].append(event)

    def _at(self, f, slot, decision, key, side, next_frame=None):
        heroes = f.get("heroes")
        if not isinstance(heroes, dict):
            return None
        def hero(s):
            h = heroes.get(str(s))
            return h if isinstance(h, dict) else {}
        actor, target, enemy = hero(slot), hero(decision["target_slot"]), hero(decision["focus_slot"])
        if not actor or not (target or enemy):
            return None
        is_item = key.startswith("item_")
        button = _button(actor, key)
        alive = _alive(actor)
        checks = [_check("actor", alive, {True:"Герой жив.", False:"Герой погиб.", None:"Нет состояния жизни."}[alive]),
                  _recipient(target, side, key), *_ready(actor, button, is_item),
                  _control(actor, is_item, side == "enemy"), _visibility(enemy, slot),
                  _reach(key, button, actor, target, side, self.patch_known, self.clean_draft)]
        if decision.get("kind") == "interrupt":
            threat = decision.get("threat_key")
            channel_button = _button(enemy, threat) if isinstance(threat, str) else {}
            chan = channel_button.get("channeling")
            flag = chan if type(chan) is bool else None
            channel_detail = {True:"Враг поддерживал это заклинание.",
                              False:"Этого канала у врага в кадре нет.", None:"Канал не подтверждён."}[flag]
            if flag:
                point = (CASTS["buttons"].get(key) or {}).get("cast_point") if self.patch_known else None
                after = (next_frame or {}).get("heroes", {}).get(str(decision["focus_slot"]), {})
                next_button = _button(after, threat)
                times = (f.get("game_time"), (next_frame or {}).get("game_time"), channel_button.get("channel_start"))
                sustained = (all(_num(t) for t in times) and _num(point) and times[0] + point <= times[1] <= times[0] + 1.1
                             and next_button.get("channeling") is True
                             and next_button.get("channel_start") == times[2])
                if sustained:
                    channel_detail += " Тот же канал продолжался после времени подготовки твоей кнопки."
                else:
                    flag, channel_detail = None, "Канал есть, но его длительность после подготовки твоей кнопки не подтверждена."
            checks.append(_check("channel", flag, channel_detail))
        states = [c["state"] for c in checks]
        status = "blocked" if "no" in states else "unknown" if "unknown" in states else "verified"
        return {"status": status, "time": f["time"], "game_time": f.get("game_time"),
                "tick": f["tick"], "checks": checks}

    def verify(self, slot, decision):
        key = decision.get("button_key")
        stamp = decision.get("time") if decision.get("time") is not None else decision.get("event_time")
        start, end = decision.get("start"), decision.get("end")
        target, focus = decision.get("target_slot"), decision.get("focus_slot")
        if not self.valid:
            return _unknown("Идентификатор или формат повтора не совпадает с карточкой.")
        if (not isinstance(key, str) or not key or any(type(t) is not int for t in (start, end, stamp)) or
                not start <= stamp <= end or any(type(s) is not int or s not in SLOTS for s in (slot, target, focus))
                or (focus < 128) == (slot < 128)):
            return _unknown("Для этой версии эпизода не установлены кнопка, участники и секунда события.")
        side = "self" if target == slot else "ally" if (target < 128) == (slot < 128) else "enemy"
        if side == "enemy" and target != focus:
            return _unknown("Цель приказа расходится с выбранным соперником.")
        if key in ACTIVE_ITEMS:
            key = "item_" + key
        witness = None
        if decision.get("event_kind") == "death":
            matches = [e for e in self.deaths[decision.get("event_actor_slot"), decision.get("event_target_slot")]
                       if abs(e["time"] - stamp) <= 1 and
                       (decision.get("event_tick") is None or e["tick"] == decision["event_tick"])]
            if len(matches) == 1:
                witness = matches[0]
        # If the exact outcome tick is absent, exclude the entire anchor
        # second. Never recommend an action after the observed death.
        hi = witness["time"] if witness else stamp - 1
        lo = max(0, start if decision.get("kind") == "interrupt" else (witness["time"] if witness else stamp) - 5)
        deadline = witness["game_time"] if witness else stamp
        for cast in self.casts[slot, key.removeprefix("item_")]:
            named_target = cast.get("target_slot")
            self_only = side == "self" and (key in SELF_DEFENSE or key in ("item_black_king_bar", "item_manta", "item_ghost"))
            if (lo <= cast["game_time"] < deadline and (named_target == target or (named_target is None and self_only))):
                return {"status":"blocked", "already_used":True, "time":cast["time"], "tick":cast["tick"],
                        "checks":[], "reason":"Повтор уже зафиксировал нажатие этой кнопки с такой целью."}
        if hi < lo:
            return _unknown("До события нет предшествующего кадра.")
        frames = (self.window(lo, hi) if callable(self.window)
                  else self.frames[bisect_left(self.times, lo):bisect_right(self.times, hi)])
        best = None
        order = None
        frames = sorted((f for f in frames if isinstance(f, dict) and type(f.get("time")) is int
                         and type(f.get("tick")) is int), key=lambda f:(f["time"],f["tick"]))
        for i, f in enumerate(frames):
            if not isinstance(f, dict) or type(f.get("time")) is not int or not lo <= f["time"] <= hi or type(f.get("tick")) is not int:
                continue
            if witness and (f["tick"] >= witness["tick"] or not _num(f.get("game_time")) or f["game_time"] >= deadline):
                continue
            # Ensure that the cast point recorded in Valve's data has time to
            # elapse before the outcome. Movement, facing and travel time can
            # still change whether the resulting effect would land.
            spec = CASTS["buttons"].get(key)
            if self.patch_known and spec and _num(f.get("game_time")) and f["game_time"] + spec["cast_point"] >= deadline:
                continue
            result = self._at(f, slot, decision, key, side, frames[i+1] if i+1 < len(frames) else None)
            if not result:
                continue
            states = [c["state"] for c in result["checks"]]
            rank = ({"verified":2, "unknown":1, "blocked":0}[result["status"]], states.count("yes"), f["time"], f["tick"])
            if order is None or rank > order:
                order, best = rank, result
        if best is None:
            return _unknown("В интервале нет кадра с нужными героями.")
        best["reason"] = {"verified":"На этой секунде условия для начала действия совпали.",
                          "blocked":"На показанной секунде есть препятствие. Среди проверенных кадров подходящего момента не найдено.",
                          "unknown":"На этой секунде часть условий удалось проверить. Для точного совета данных недостаточно."}[best["status"]]
        return best
