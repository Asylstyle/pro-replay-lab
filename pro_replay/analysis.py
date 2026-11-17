"""Measurements from observed OpenDota player events and minute snapshots."""

from collections import Counter, defaultdict
from statistics import median


# Explicit scope, stable across runs. Only full keys appearing in purchase_log count.
# Recipes, components and final inventory do not imply a purchase timestamp.
CORE_ITEMS = frozenset("""
    abyssal_blade aeon_disk aether_lens aghanims_shard arcane_boots armlet assault
    basher bfury black_king_bar blade_mail blink bloodstone bloodthorn boots_of_bearing
    butterfly crimson_guard cyclone desolator diffusal_blade disperser
    dragon_lance echo_sabre ethereal_blade eternal_shroud force_staff
    glimmer_cape greater_crit gungir guardian_greaves hand_of_midas harpoon heart
    heavens_halberd helm_of_the_dominator helm_of_the_overlord hurricane_pike
    invisible_edge kaya_and_sange khanda lotus_orb maelstrom mage_slayer manta mask_of_madness
    meteor_hammer mekansm mjollnir monkey_king_bar moon_shard nullifier
    octarine_core orchid overwhelming_blink pavise phase_boots phylactery pipe
    power_treads radiance refresher revenants_brooch rod_of_atos sange_and_yasha
    satanic sheepstick shivas_guard silver_edge skadi solar_crest
    soul_ring sphere spirit_vessel swift_blink tranquil_boots travel_boots travel_boots_2
    ultimate_scepter vanguard veil_of_discord wind_waker witch_blade
    wraith_pact yasha_and_kaya
""".split())


def _int(value):
    """Accept integer JSON fields and their decimal-string equivalents."""
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _nonnegative(value):
    value = _int(value)
    return value if value is not None and value >= 0 else None


def _side(player):
    slot = _int(player.get("player_slot"))
    if slot in range(5):
        return "radiant"
    if slot in range(128, 133):
        return "dire"
    radiant = player.get("isRadiant")
    if isinstance(radiant, bool):
        return "radiant" if radiant else "dire"
    return None


def _team_kills(match, side, players):
    if side is None:
        return None
    score = _nonnegative(match.get(f"{side}_score"))
    if score is not None:
        return score
    team = [p for p in players if _side(p) == side]
    kills = [_nonnegative(p.get("kills")) for p in team]
    if len(team) == 5 and len({p.get("player_slot") for p in team}) == 5 and None not in kills:
        return sum(kills)
    return None


def _minute_ten(player, duration):
    if duration is not None and duration < 600:
        return None, None, None
    times = player.get("times")
    # OpenDota's standard minute arrays start at t=0. When explicit timestamps
    # exist, require an exact 600s sample even for truncated/irregular arrays.
    index = 10
    if times is not None:
        if not isinstance(times, list):
            return None, None, None
        index = next((i for i, t in enumerate(times) if _int(t) == 600), None)
        if index is None:
            return None, None, None

    def at_ten(key):
        values = player.get(key)
        if isinstance(values, list) and index < len(values):
            return _nonnegative(values[index])
        return None

    return at_ten("lh_t"), at_ten("gold_t"), at_ten("xp_t")


def _purchases(raw_log):
    purchases = []
    if isinstance(raw_log, list):
        for event in raw_log:
            if not isinstance(event, dict):
                continue
            time, key = _int(event.get("time")), event.get("key")
            if time is not None and isinstance(key, str) and key:
                purchases.append({"time": time, "key": key, "item": key})
    # Stable sort preserves the source order when multiple events share a second.
    return sorted(purchases, key=lambda event: event["time"])


def select_player(match, account_id, slot=None):
    """An anonymous slot identifies a player only within this match."""
    players = match.get("players")
    field, value = ("player_slot", slot) if slot is not None else ("account_id", account_id)
    if not isinstance(players, list) or _int(value) is None:
        return None
    if field == "account_id" and not 0 < _int(value) < 2**32 - 1:
        return None
    hits = [p for p in players if isinstance(p, dict) and _int(p.get(field)) == _int(value)]
    return hits[0] if len(hits) == 1 else None


def extract_match(match: dict, account_id: int | None, *, player=None) -> dict | None:
    """Extract a single observed player's match, retaining absent values as None."""
    if player is None:
        player = select_player(match, account_id)
    if player is None:
        return None
    target = _int(player.get("account_id"))
    if target is not None and not 0 < target < 2**32 - 1:
        target = None
    players = [p for p in match["players"] if isinstance(p, dict)]

    side = _side(player)
    radiant_win = match.get("radiant_win")
    win = None
    if isinstance(radiant_win, bool) and side is not None:
        win = radiant_win == (side == "radiant")

    team_kills = _team_kills(match, side, players)
    kills, deaths, assists = (_nonnegative(player.get(k)) for k in ("kills", "deaths", "assists"))
    participation = None
    if team_kills and kills is not None and assists is not None and kills + assists <= team_kills:
        participation = (kills + assists) / team_kills

    duration = _nonnegative(match.get("duration"))
    lh, gold, xp = _minute_ten(player, duration)
    log = player.get("purchase_log")
    purchases = _purchases(log)
    core = [p for p in purchases if p["key"] in CORE_ITEMS]
    complete_log = isinstance(log, list) and len(log) == len(purchases)
    first = {}
    if complete_log and core:
        first_time = core[0]["time"]
        keys = {event["key"] for event in core if event["time"] == first_time}
        if len(keys) == 1:
            first = core[0]
    return {
        "match_id": _int(match.get("match_id")),
        "account_id": target,
        "is_demo": any(match.get(flag) is True for flag in ("demo", "is_demo", "synthetic")),
        "hero_id": _int(player.get("hero_id")),
        "patch": match.get("patch"),
        "start_time": _int(match.get("start_time")),
        "duration": duration,
        "league_id": _int(match.get("leagueid")),
        "lobby_type": _int(match.get("lobby_type")),
        "game_mode": _int(match.get("game_mode")),
        "player_slot": _int(player.get("player_slot")),
        "side": side,
        "win": win,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "team_kills": team_kills,
        "kill_participation": participation,
        "last_hits_10": lh,
        "lh_10": lh,
        "gold_10": gold,
        "xp_10": xp,
        "purchase_log_available": complete_log,
        "purchases": purchases,
        "core_items": core,
        "first_core_item": first.get("key"),
        "first_core_time": first.get("time"),
    }


def _middle(values):
    return median(values) if values else None


def _group_counts(rows, field):
    totals = Counter(row[field] for row in rows if row.get(field) is not None)
    return [{field: value, "matches": count}
            for value, count in sorted(totals.items(), key=lambda pair: (-pair[1], str(pair[0])))]


def first_pair(row):
    core = row.get("core_items", [])
    if (row.get("purchase_log_available") and len(core) >= 2
            and core[0]["time"] < core[1]["time"]
            and (len(core) == 2 or core[1]["time"] < core[2]["time"])):
        return core[0]["key"], core[1]["key"]
    return None


def summarize(rows: list[dict]) -> dict:
    """Aggregate one row per match, keeping a denominator for every metric."""
    first_times = defaultdict(list)
    all_times = defaultdict(list)
    pairs = Counter()
    for row in rows:
        first = row.get("first_core_item")
        if first is not None:
            first_times[first].append(row["first_core_time"])
        seen = set()
        for event in row.get("core_items", []):
            key = event["key"]
            if key not in seen:
                all_times[key].append(event["time"])
                seen.add(key)
        pair = first_pair(row)
        if pair:
            pairs[pair] += 1

    hits = [r["last_hits_10"] for r in rows if r.get("last_hits_10") is not None]
    participation = [r["kill_participation"] for r in rows if r.get("kill_participation") is not None]
    outcomes = [r["win"] for r in rows if r.get("win") is not None]
    first_count = sum(len(times) for times in first_times.values())
    pair_count = sum(pairs.values())
    first_core_items = [
        {"item": key, "matches": len(times), "share": len(times) / first_count, "median_time": median(times)}
        for key, times in sorted(first_times.items(), key=lambda p: (-len(p[1]), p[0]))
    ]
    item_timings = [
        {"item": key, "matches": len(times), "median_time": median(times)}
        for key, times in sorted(all_times.items(), key=lambda p: (-len(p[1]), p[0]))
    ]
    return {
        "matches": len(rows),
        "coverage": {
            "purchase_logs": sum(bool(row.get("purchase_log_available")) for row in rows),
            "first_core": first_count,
            "first_two_core": pair_count,
            "last_hits_10": len(hits),
            "kill_participation": len(participation),
            "outcomes": len(outcomes),
        },
        "first_core_items": first_core_items,
        "first_two_core_items": [
            {"items": list(pair), "matches": count, "share": count / pair_count}
            for pair, count in sorted(pairs.items(), key=lambda p: (-p[1], p[0]))
        ],
        "item_timings": item_timings,
        "median_last_hits_10": _middle(hits),
        "median_kill_participation": _middle(participation),
        "wins": sum(outcomes),
        "by_hero": _group_counts(rows, "hero_id"),
        "by_patch": _group_counts(rows, "patch"),
    }
