"""Evaluate a support rotation from past replay frames and the current cast state."""

from collections import Counter, deque
from functools import lru_cache
from html import escape

from .mid_spells import hero_profile, rank_value, spell_specs
from .replay_verification import _bits, _control, _distance, _num, _ready, _recipient
from .report import _clock


# Only direct, unit-targeted disables with an unconditional first effect.
# Use the lifted target's duration for Telekinesis and the minimum for Chaos Bolt.
DISABLE_FIELDS = {
    "rubick_telekinesis": "lift_duration", "lion_voodoo": "duration",
    "shadow_shaman_voodoo": "duration", "shadow_shaman_shackles": "channel_time",
    "beastmaster_primal_roar": "duration", "ogre_magi_fireblast": "stun_duration",
    "luna_lucent_beam": "stun_duration", "sven_storm_bolt": "bolt_stun_duration",
    "vengefulspirit_magic_missile": "magic_missile_stun",
    "witch_doctor_paralyzing_cask": "hero_duration",
    "skeleton_king_hellfire_blast": "blast_stun_duration", "chaos_knight_chaos_bolt": "stun_min",
}
STATE_PRIORITY = {"ready": 6, "approaching": 5, "in_range": 4, "blocked": 3, "unknown": 2, "no_approach": 1}
SEARCH_RADIUS = 1500


@lru_cache(maxsize=None)
def _controls(hero_id):
    return [(key, spec, next(p["values"] for p in spec["parameters"] if p["field"] == DISABLE_FIELDS[key]))
            for key, spec in spell_specs(hero_id).items() if key in DISABLE_FIELDS]


def _roster(raw, slot):
    team_slots = range(0, 5) if slot < 128 else range(128, 133)
    side = [p for p in raw.get("players") or () if isinstance(p, dict)
            and type(p.get("player_slot")) is int and p["player_slot"] in team_slots]
    slots = Counter(p["player_slot"] for p in side)
    roles = Counter(p.get("position_est") for p in side if type(p.get("position_est")) is int)
    return [(p["player_slot"], p["hero_id"], p["position_est"]) for p in side
            if slots[p["player_slot"]] == 1 and p["player_slot"] != slot and type(p.get("hero_id")) is int
            and type(p.get("position_est")) is int and p["position_est"] in (4, 5) and roles[p["position_est"]] == 1]


def _visible(hero, team):
    return isinstance(hero.get("visible_to"), dict) and hero["visible_to"].get(str(team)) is True


def _approach(history, slot, hero_id, enemy, enemy_id, team):
    """Require three consecutive seconds of approach, including the support's own movement."""
    if len(history) < 4:
        return None
    positions = []
    for f in history:
        units = f.get("heroes")
        if not isinstance(units, dict):
            return None
        ally, rival = units.get(str(slot)), units.get(str(enemy))
        if not (isinstance(ally, dict) and ally.get("hero_id") == hero_id and ally.get("alive") is True and
                isinstance(rival, dict) and rival.get("hero_id") == enemy_id and rival.get("alive") is True
                and _visible(rival, team) and _distance(ally, rival) is not None):
            return None
        positions.append((ally, rival))
    end_target = positions[-1][1]
    projected = [_distance(a, end_target) for a, _ in positions]
    separation = [_distance(a, r) for a, r in positions]
    for (old, old_enemy), (new, new_enemy) in zip(positions, positions[1:]):
        if max(_distance(old, new), _distance(old_enemy, new_enemy)) > 600:
            return None  # discontinuity or mobility spell
    steps = [a-b for a, b in zip(projected, projected[1:])]
    closing = (projected[0]-projected[-1] >= 120 and separation[0]-separation[-1] >= 100
               and sum(s >= 20 for s in steps) >= 2 and steps[-1] >= -10)
    return {"closing": closing, "start": history[0]["time"], "before": round(separation[0])}


def _button_review(ally, rival, distance, clean_range, key, spec, durations):
    buttons = ally.get("buttons")
    button = buttons.get(key) if isinstance(buttons, dict) else None
    if not isinstance(button, dict) or button.get("kind") != "ability":
        return {"usable": None, "reason": "В кадре нет состояния способности."}
    rank = button.get("level")
    row = {"key": key, "skill": spec["name"], "rank": rank, "mana": ally.get("mana"),
           "cost": button.get("mana_cost"), "cooldown": button.get("cooldown")}
    if type(rank) is not int or rank < 0 or rank > spec["max_rank"]:
        return dict(row, usable=None, reason="Ранг контроля в кадре не подтверждён.")
    geometry = button.get("cast_range")
    if not (_num(geometry) and geometry > 0):
        geometry = rank_value(spec["range"], rank) if clean_range else None
        if _num(geometry) and ally.get("hero_id") == 86 and _bits(ally, (10,)) is False:
            passive = (ally.get("buttons") or {}).get("rubick_arcane_supremacy") or {}
            spec_passive = spell_specs(86)["rubick_arcane_supremacy"]
            bonus = next((p["values"] for p in spec_passive["parameters"] if p["field"] == "cast_range"), None)
            geometry += rank_value(bonus, passive.get("level")) or 0
    if _num(geometry) and geometry > 0:
        row.update(reach=geometry, in_reach=distance <= geometry)
    checks = [*_ready(ally, button, False), _control(ally, False, True), _recipient(rival, "enemy", key)]
    problem = next((c for c in checks if c["state"] == "no"),
                   next((c for c in checks if c["state"] != "yes"), None))
    if problem:
        return dict(row, usable=False if problem["state"] == "no" else None, reason=problem["detail"])
    barriers = {"sphere", "mirror_shield", "antimage_counterspell", "antimage_counterspell_ally"}
    if _bits(rival, (7,)) is not False or not isinstance(rival.get("buttons"), dict):
        return dict(row, usable=None, reason="Видимость цели для направленного навыка или её защита не установлены.")
    for name, b in rival["buttons"].items():
        if name.removeprefix("item_") in barriers and isinstance(b, dict):
            if b.get("kind") == "item" and type(b.get("slot")) is int and b["slot"] >= 6:
                continue
            if b.get("kind") == "ability" and b.get("level") == 0:
                continue
            return dict(row, usable=None, reason="У цели возможна защита от направленного навыка.")
    cast = rank_value(spec["cast_point"], rank)
    duration = rank_value(durations, rank)
    proof = spec.get("proof") or {}
    speed = rank_value(proof.get("speed"), rank)
    delay = rank_value(proof.get("delay"), rank) or 0
    if not (_num(geometry) and geometry > 0 and _num(cast) and _num(duration) and duration > 0):
        return dict(row, usable=None, reason="Дальность или время действия контроля не подтверждены.")
    return dict(row, usable=True, arrival=round(cast + delay + (distance/speed if speed else 0), 2),
                duration=duration, reason="Способность изучена и доступна.")


class SupportTracker:
    def __init__(self, raw, slot, enemy, enemy_id):
        self.roster = _roster(raw, slot)
        self.team = 2 if slot < 128 else 3
        self.enemy, self.enemy_id = enemy, enemy_id
        self.history = deque(maxlen=4)

    def observe(self, frame):
        if self.history:
            prior = self.history[-1]
            elapsed = frame.get("game_time")
            old = prior.get("game_time")
            if (frame["time"] != prior["time"]+1 or frame["tick"] <= prior["tick"] or
                    not (_num(elapsed) and _num(old) and 0.8 <= elapsed-old <= 1.2)):
                self.history.clear()
        self.history.append(frame)

    def review(self, clean_range):
        if not self.history or not self.roster:
            return {"state": "unknown", "reason": "Нет кадра или однозначной оценки позиции 4/5."}
        units = self.history[-1].get("heroes")
        if not isinstance(units, dict):
            return {"state": "unknown", "reason": "В кадре нет состояний героев."}
        rival = units.get(str(self.enemy))
        if not (isinstance(rival, dict) and rival.get("hero_id") == self.enemy_id and _visible(rival, self.team)):
            return {"state": "unknown", "reason": "Вражеский мидер не виден команде."}
        reviews = []
        for slot, hero_id, role in self.roster:
            ally = units.get(str(slot))
            if not isinstance(ally, dict) or ally.get("hero_id") != hero_id or ally.get("alive") is not True:
                continue
            distance = _distance(ally, rival)
            if distance is None or distance > SEARCH_RADIUS:
                continue
            movement = _approach(self.history, slot, hero_id, self.enemy, self.enemy_id, self.team)
            row = {"slot": slot, "hero": hero_profile(hero_id).get("name", str(hero_id)), "role": role,
                   "time": self.history[-1]["time"], "distance": round(distance), "movement": movement,
                   "target_disabled": _bits(rival, (5, 6))}
            buttons = [_button_review(ally, rival, distance, clean_range, key, spec, durations)
                       for key, spec, durations in _controls(hero_id)]
            if not buttons:
                buttons = [{"usable": None, "reason": "Для навыков этого саппорта нет проверенного направленного контроля."}]
            for button in buttons:
                closing = bool(movement and movement["closing"])
                if button["usable"] is True and button["in_reach"]:
                    state = "ready" if closing else "in_range"
                elif closing:
                    state = ("approaching" if button["usable"] is True else
                             "blocked" if button["usable"] is False else "unknown")
                else:
                    continue
                reviews.append(dict(row, state=state, button=button, reason=button["reason"]))
        return max(reviews, key=lambda r:(STATE_PRIORITY[r["state"]], r["button"].get("rank") or 0,
                                          -r["distance"]), default={
            "state": "no_approach", "reason": "Подход живого саппорта к видимой цели по кадрам не подтверждён."})


def render_support(card, enemy):
    check = card.get("support")
    if not check:
        return ""
    if "hero" not in check:
        return "<p>Поддержка размена · " + escape(check["reason"]) + "</p>"
    hero = escape(check["hero"])
    parts = [f'<p>Саппорт · {hero}, позиция {check["role"]} по оценке OpenDota. ']
    move, b = check["movement"], check["button"]
    if move and move["closing"]:
        parts.append(f'С {_clock(move["start"])} по {_clock(check["time"])} двигался к {escape(enemy)}. '
                     f'Дистанция сократилась с {move["before"]} до {check["distance"]} ед. ')
    else:
        parts.append(f'Дистанция до {escape(enemy)} — {check["distance"]} ед. Подход по предыдущим кадрам не подтверждён. ')
    if b.get("skill"):
        parts.append(f'{escape(b["skill"])} · ранг {b["rank"]}. ')
    if b["usable"] is True:
        parts.append(f'Перезарядка {b["cooldown"]:g} с, маны {b["mana"]:.0f} при расходе {b["cost"]:g}, '
                     f'проверенная дальность {b["reach"]:g} ед. ')
        if not b["in_reach"]:
            parts.append('Дистанция в кадре превышает проверенную дальность. Точную границу могли менять размеры героев и бонусы. ')
        else:
            parts.append(f'Расчёт до начала контроля — {b["arrival"]:g} с без поворота. ')
    else:
        parts.append(escape(b["reason"]) + " ")
    if check.get("target_disabled"):
        parts.append('К этому моменту враг уже находился под оглушением или Hex. '
                     'Оставшаяся длительность предыдущего контроля в кадре не измерена. ')
    if card.get("coordinated"):
        parts.append(f'Вариант вдвоём · дождись попадания {escape(b["skill"])}, затем примени '
                     f'{escape(card["own"]["spell"])} по {escape(enemy)}, пока действует контроль. '
                     'Проверь поворот, сопротивление эффектам и фактическое срабатывание в повторе.')
    if check.get("observed"):
        observed = check["observed"]
        stamp = _clock(observed["time"])
        if "game_time" in observed:
            whole, hundredths = divmod(round(observed["game_time"] * 100), 100)
            stamp = f"{_clock(whole)},{hundredths:02d}"
        parts.append(f' В {stamp} журнал фиксирует применение {escape(b["skill"])} '
                     f'этим саппортом по {escape(enemy)}.')
    return "".join(parts) + "</p>"


def annotate_support_casts(cards, events, enemy):
    """Show subsequent casts separately from the evidence available at the decision tick."""
    casts = [e for e in events if isinstance(e, dict) and e.get("kind") == "cast"
             and e.get("target_slot") == enemy and type(e.get("tick")) is int and type(e.get("time")) is int]
    for card in cards:
        s = card.get("support") or {}
        key = (s.get("button") or {}).get("key")
        if not key:
            continue
        seen = min((e for e in casts if e.get("actor_slot") == s["slot"] and e.get("button_key") == key
                    and e["tick"] > card["tick"] and 0 <= e["time"] - card["time"] <= 2
                    and (not _num(e.get("game_time")) or 0 <= e["game_time"] - card["time"] <= 2)),
                   key=lambda e:e["tick"], default=None)
        if seen:
            s["observed"] = {"time": seen["time"], "tick": seen["tick"]}
            if _num(seen.get("game_time")):
                s["observed"]["game_time"] = seen["game_time"]
