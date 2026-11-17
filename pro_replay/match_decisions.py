"""Select distinct, consequential teamfight decisions for one player's match."""

from .decision_actions import decision_options
from .decision_events import known_dead_at, observed_events
from .report import _clock


def _anchor(option, events, slot):
    """Use the action engine's witness; a death timestamp is not a cast timestamp."""
    stamp = option.get("event_time")
    if stamp is not None:
        return next((e for e in events if e["time"] == stamp and
                     all(option.get(key) is None or option[key] == e[field] for key, field in
                         (("event_kind", "kind"), ("event_actor_slot", "actor_slot"), ("event_target_slot", "target_slot")))),
                    None)
    # If no witness was attached to this option, anchor only to the death of
    # the actual beneficiary. Unrelated kills in the same window do not qualify.
    beneficiary = slot if option["kind"] == "self_defense" else option.get("target_slot")
    if option["kind"] in ("self_defense", "protect") and beneficiary is not None:
        return next((e for e in events if e["kind"] == "death" and e["target_slot"] == beneficiary), None)
    return None


def _stakes(context, events, side, slot):
    own_loss = any(e["kind"] == "death" and e["target_slot"] == slot for e in events) or bool(context.get("deaths"))
    losses = context.get("team", {}).get("deaths")
    enemy_losses = context.get("enemy", {}).get("deaths")
    observed_losses = sum(e["kind"] == "death" and e["perspective"] in ("self", "ally") for e in events)
    count = losses if losses is not None else observed_losses
    objectives = [obj for obj in context.get("objectives_after", []) if obj.get("lost_side") == side]
    bought_back = bool(context.get("buybacks")) or any(e["kind"] == "buyback" and e["actor_slot"] == slot for e in events)
    if not (count or own_loss or bought_back or objectives):
        return None
    facts = []
    if losses is not None and enemy_losses is not None and (losses or enemy_losses):
        facts.append(f"Смертей у твоей команды — {losses}, у соперника — {enemy_losses}.")
    elif own_loss:
        facts.append("Твой герой погиб в этой драке.")
    elif count:
        facts.append(f"В этом интервале зафиксированы смерти союзников ({count}).")
    if bought_back:
        facts.append("В журнале отмечен твой выкуп.")
    if objectives:
        obj = objectives[0]
        facts.append(f'{_clock(obj["time"])} · {obj["label"]}.')
    disadvantage = min(3, max(0, losses - enemy_losses)) if losses is not None and enemy_losses is not None else 0
    priority = 2 * int(own_loss) + disadvantage + 2 * int(bool(objectives)) + int(bought_back)
    return priority, " ".join(facts)


def _pro_comparison(option, fight):
    enemy = option.get("enemy_focus")
    for item in fight["items"]:
        stat = item.get("fight_pro")
        if stat and item["name"] == option["button"] and enemy == stat["enemy"]:
            return (f'В {stat["uses"]} из {stat["n"]} похожих про-драк игроки этой позиции применили '
                    f'{item["name"]}, купленный до боя. У {enemy} в этих окнах отмечено {stat["threat"]}.', None)
    stat = fight.get("pro_action")
    if stat and stat["ability"] == option["button"] and stat["enemy"] == enemy:
        return (f'На том же герое и позиции {stat["ability"]} применяли в {stat["count"]} из {stat["n"]} '
                f'похожих про-драк с {stat["threat"]} у {enemy}.',
                f'https://www.opendota.com/matches/{int(stat["example"])}/teamfights')
    return None, None


def _timing(option, event, start):
    """Tell the player what cue to look for; the logged second locates its outcome."""
    when = event["time"] if event else None
    review_from = max(0, when - 5) if when is not None else max(0, start)
    if option["kind"] == "interrupt":
        return ("Во время канала. Найди начало каста у " + option["enemy_focus"] + " в этой драке.",
                min(max(0, start), review_from))
    if not event or not event.get("target"):
        return "Найди момент по последовательности действий в драке.", review_from
    victim = event["target"]
    if event["kind"] == "death":
        return f"Пока {victim} ещё жив, до последнего удара на {_clock(when)}.", review_from
    if event["kind"] == "hero_hit":
        return f"Проверь подход к попаданию по {victim} на {_clock(when)}.", review_from
    return "Проверь положение героев перед событием.", review_from


def _distinct_decisions(candidates):
    """Find the highest-ranked feasible set of up to three independent decisions."""
    ordered = [d for _, d in sorted(candidates, key=lambda pair: pair[0], reverse=True)]

    def compatible(a, b):
        separate = a["end"] < b["start"] or b["end"] < a["start"]
        return separate and (a["button"], a["target"], a.get("enemy_focus")) != (
                             b["button"], b["target"], b.get("enemy_focus"))

    def choose(pool, count):
        if count == 1:
            return pool[:1]
        for idx, first in enumerate(pool):
            remaining = [d for d in pool[idx+1:] if compatible(first, d)]
            if len(remaining) >= count - 1:
                rest = choose(remaining, count - 1)
                if rest:
                    return [first, *rest]
        return []

    for count in (3, 2, 1):
        chosen = choose(ordered, count)
        if chosen:
            return chosen
    return []


def top_decisions(raw, slot, heroes, fights, detailed_intervals, *, reviewer=None, extra=(), excluded=None):
    contexts = {(c["start"], c["end"]): c for c in detailed_intervals}
    side = "radiant" if slot < 128 else "dire"
    candidates = []
    life_checks = {}

    def already_dead(who, when):
        key = (who, when)
        if key not in life_checks:
            life_checks[key] = known_dead_at(raw, who, heroes, when, before=True)
        return life_checks[key]

    for fight in fights:
        start, end = fight["start"], fight["end"]
        events = observed_events(raw, slot, heroes, fight)
        context = contexts.get((start, end), {})
        stakes = _stakes(context, events, side, slot)
        if stakes is None:
            continue
        # Share the same evidence with the focus/button selector.
        options = decision_options(raw, slot, heroes, {**fight, "observed_events": events})
        for option in options:
            anchor = _anchor(option, events, slot)
            if option.get("event_time") is not None and anchor is None:
                continue
            when = anchor["time"] if anchor else None
            actors = {slot, option.get("focus_slot"), option.get("target_slot")} - {None}
            if when is not None and any(already_dead(who, when) for who in actors):
                continue
            death_seconds = anchor.get("time_dead", 0) if anchor and anchor["kind"] == "death" else 0
            importance = stakes[1]
            if death_seconds:
                importance += f' В журнале смерти героя {anchor["target"]} указано {death_seconds} сек. вне игры.'
            timing, review_from = _timing(option, anchor, start)
            decision = {**option, "start": start, "end": end, "time": when,
                        "event": anchor["text"] if anchor else "Действия записаны суммарно за интервал.",
                        "timing": timing, "review_from": review_from,
                        "importance": importance.strip()}
            decision["pro_note"], decision["pro_url"] = _pro_comparison(option, fight)
            # Evidence leads. Among equally supported options, prefer a concrete
            # witness and material losses. The tie break is stable across runs.
            priority = (option["strength"], int(anchor is not None) + stakes[0],
                        int(anchor is not None), death_seconds, -start)
            candidates.append((priority, decision))
    if reviewer is None:
        return _distinct_decisions(candidates)
    verified, rejected = [], []
    for priority, decision in [*candidates, *extra]:
        result = reviewer.verify(slot, decision)
        decision["verification"] = result
        if result["status"] == "verified":
            decision["review_from"] = max(0, result["time"] - 2)
            verified.append((priority, decision))
        else:
            rejected.append((priority, decision))
    chosen = _distinct_decisions(verified)
    if excluded is not None:
        # Show why another tempting play did not qualify, without repeating a
        # button/target pair that was feasible elsewhere in the same fight.
        def identity(d):
            return d["start"], d["end"], d["button"], d["target"], d.get("enemy_focus")
        accepted = {identity(d) for _, d in verified}
        remaining = [(p, d) for p, d in rejected if identity(d) not in accepted]
        excluded.extend(_distinct_decisions(remaining))
    return chosen
