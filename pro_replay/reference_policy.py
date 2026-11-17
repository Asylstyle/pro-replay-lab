"""Eligibility for current-patch economy references from OpenDota match data."""

from collections import Counter
from datetime import datetime, timezone
from re import fullmatch

from .coach_model import _game
from .impact import SLOTS
from .source import VALVE_PATCHES


LIVE_SOURCE = "https://api.opendota.com/api/live"
MINIMUM_AVG_MMR = 7300
FORMAT = "opendota-patch-references-v1"


def _positive_int(value):
    return type(value) is int and value > 0


def _match_id(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    return value if _positive_int(value) else None


def _release(calendar, when):
    if not _positive_int(when):
        raise ValueError("Expected a positive integer Unix timestamp.")
    releases = {}
    for stamp, version in calendar:
        if not _positive_int(stamp) or not isinstance(version, str) or not fullmatch(r"[0-9]+\.[0-9]+[a-z]?", version):
            raise ValueError("Invalid Valve patch calendar entry.")
        if stamp in releases and releases[stamp] != version:
            raise ValueError("Conflicting Valve patch releases at the same timestamp.")
        releases[stamp] = version
    stamp = max((t for t in releases if t <= when), default=None)
    if stamp is None:
        raise ValueError("No Valve patch available at that timestamp.")
    return stamp, releases[stamp]


def resolve_patch(calendar, start):
    """Resolve the exact Valve release at match start using a refreshed calendar."""
    return _release(calendar, start)[1]


def make_policy(calendar, *, as_of):
    """Freeze the effective patch and the latest complete-data timestamp in UTC."""
    stamp, patch = _release(calendar, as_of)
    return {"format": FORMAT, "calendar_source": VALVE_PATCHES, "patch": patch,
            "patch_start": stamp, "as_of": as_of, "minimum_avg_mmr": MINIMUM_AVG_MMR}


def _observed_second(text):
    if not isinstance(text, str):
        raise ValueError("Missing timestamp of the OpenDota /live observation.")
    stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("OpenDota observation requires an explicit UTC offset.")
    return int(stamp.astimezone(timezone.utc).timestamp())


def compact_live_evidence(live, *, observed_at_utc):
    """Normalize one row returned by OpenDota /live. Omit names and account IDs.

    average_mmr is the upstream lobby-level number. The spectator feed does not
    provide the number of contributing ratings.
    """
    if not isinstance(live, dict):
        raise ValueError("Expected one OpenDota /live match.")
    mid = _match_id(live.get("match_id"))
    rating, updated = live.get("average_mmr"), live.get("last_update_time")
    if not mid or not _positive_int(rating):
        raise ValueError("Missing live match ID or numeric lobby average MMR.")
    if not _positive_int(updated) or updated > _observed_second(observed_at_utc):
        raise ValueError("Invalid live update or observation time.")
    players = live.get("players")
    if not isinstance(players, list) or len(players) != 10:
        raise ValueError("OpenDota spectator roster is incomplete.")
    roster = []
    for p in players:
        if not isinstance(p, dict):
            raise ValueError("Invalid live player.")
        team, slot, hero = p.get("team"), p.get("team_slot"), p.get("hero_id")
        if type(team) is not int or team not in (0, 1) or type(slot) is not int or not 1 <= slot <= 5 or not _positive_int(hero):
            raise ValueError("Incomplete spectator hero or team slot.")
        roster.append({"player_slot": 128 * team + slot - 1, "hero_id": hero})
    if {p["player_slot"] for p in roster} != set(SLOTS):
        raise ValueError("Repeated or missing live roster slots.")
    return {"source": LIVE_SOURCE, "match_id": mid, "average_mmr": rating,
            "last_update_time": updated, "observed_at_utc": observed_at_utc,
            "lobby_type": live.get("lobby_type"), "game_mode": live.get("game_mode"),
            "league_id": live.get("league_id"),
            "roster": sorted(roster, key=lambda p: p["player_slot"])}


def _teams(players):
    if not isinstance(players, list) or len(players) != 10:
        return None
    slots = set()
    teams = Counter()
    for p in players:
        if not isinstance(p, dict):
            return None
        slot, hero = p.get("player_slot"), p.get("hero_id")
        if type(slot) is not int or slot not in SLOTS or slot in slots or not _positive_int(hero):
            return None
        slots.add(slot)
        teams[(slot >= 128, hero)] += 1
    return teams


def reference_eligibility(raw, policy, *, public_match=None):
    """Return (source cohort, None) or (None, exclusion reason).

    public_match is a normalized /live observation from compact_live_evidence.
    Archived compact matches may instead embed that observation in opendota_live.
    Both paths apply identical provenance, match identity and roster checks.
    """
    if (not isinstance(policy, dict) or policy.get("format") != FORMAT
            or policy.get("calendar_source") != VALVE_PATCHES or policy.get("minimum_avg_mmr") != MINIMUM_AVG_MMR
            or not _positive_int(policy.get("patch_start")) or not _positive_int(policy.get("as_of"))
            or policy["patch_start"] > policy["as_of"] or not isinstance(policy.get("patch"), str)
            or not fullmatch(r"[0-9]+\.[0-9]+[a-z]?", policy["patch"])):
        raise ValueError("Invalid or unsupported reference policy.")
    if not isinstance(raw, dict):
        return None, "invalid_match"
    start, duration = raw.get("start_time"), raw.get("duration")
    if not _positive_int(start) or not _positive_int(duration):
        return None, "missing_match_metadata"
    if start < policy["patch_start"]:
        return None, "outside_reference_patch"
    if start + duration > policy["as_of"]:
        return None, "match_unfinished_at_cutoff"
    teams = _teams(raw.get("players"))
    if raw.get("human_players") != 10 or teams is None:
        return None, "incomplete_ten_human_roster"
    ranked = raw.get("leagueid") == 0
    _, reason = _game(raw, ranked=ranked)
    if reason:
        return None, reason
    if not ranked:
        league = raw.get("league", {})
        if league.get("leagueid") is not None and league["leagueid"] != raw["leagueid"]:
            return None, "league_identity_mismatch"
        return "professional", None
    evidence = raw.get("opendota_live") if public_match is None else public_match
    if not isinstance(evidence, dict) or evidence.get("source") != LIVE_SOURCE:
        return None, "missing_verified_lobby_mmr"
    if (_match_id(evidence.get("match_id")) != _match_id(raw.get("match_id"))
            or (evidence.get("lobby_type"), evidence.get("game_mode"), evidence.get("league_id")) != (7, 22, 0)):
        return None, "live_match_identity_mismatch"
    updated = evidence.get("last_update_time")
    try:
        observed = _observed_second(evidence.get("observed_at_utc"))
    except (ValueError, OverflowError, OSError):
        return None, "invalid_live_observation_time"
    if not _positive_int(updated) or not updated <= observed <= policy["as_of"]:
        return None, "invalid_live_observation_time"
    if _teams(evidence.get("roster")) != teams:
        return None, "live_roster_mismatch"
    rating = evidence.get("average_mmr")
    if not _positive_int(rating):
        return None, "missing_verified_lobby_mmr"
    if rating < MINIMUM_AVG_MMR:
        return None, "below_7300_average_mmr"
    return "ranked_7300", None
