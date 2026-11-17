"""Compare logged purchases and fight actions within the client's population."""

import gzip
import json
import os
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from statistics import median
from tempfile import NamedTemporaryFile

from .analysis import _purchases
from .impact import SLOTS
from .fight_coach import ITEM_THREATS, SPELL_LOOKUP
from .fight_review import _counts, _spell_name
from .reference_policy import reference_eligibility


REFERENCE = Path(__file__).resolve().parent.parent / "examples" / "pro-fight-purchases.json.gz"
RANKED_REFERENCE = REFERENCE.with_name("ranked-7300-fight-purchases.json.gz")


def peer_fights(fight):
    return "драк в пабах 7 300+" if fight.get("peer_cohort") == "ranked_7300" else "про-драк"


def _fight_cases(raw):
    result = []
    seen = set()
    for fight in raw.get("teamfights") or []:
        if not isinstance(fight, dict):
            continue
        parts, start, end = fight.get("players"), fight.get("start"), fight.get("end")
        if (type(start) is not int or type(end) is not int or end < start or start > raw["duration"] or
                (start, end) in seen or
                not isinstance(parts, list) or len(parts) != 10):
            continue
        seen.add((start, end))
        actors = []
        for actor in parts:
            actor = actor if isinstance(actor, dict) else {}
            spells = _counts(actor.get("ability_uses"))
            items = _counts(actor.get("item_uses"))
            active = spells or items or any(isinstance(actor.get(k), (float, int)) and actor[k] > 0
                                           for k in ("deaths", "damage", "healing"))
            actors.append({"abilities": sorted(spells) if spells is not None else None,
                           "items": sorted(items) if items is not None else None} if active else None)
        result.append({"start": start, "actors": actors})
    return result

def _training_game(raw, tracked):
    players = raw.get("players")
    if not isinstance(players, list) or any(not isinstance(p, dict) for p in players):
        return None
    by_slot = {p.get("player_slot"): p for p in players}
    if len(players) != 10 or set(by_slot) != set(SLOTS):
        return None
    teams = []
    for slots in (SLOTS[:5], SLOTS[5:]):
        side = []
        for slot in slots:
            p = by_slot[slot]
            hid, role = p.get("hero_id"), p.get("position_est")
            if type(hid) is not int or hid <= 0:
                return None
            log = p.get("purchase_log")
            records = _purchases(log)
            first = None
            if records and len(records) == len(log):
                first = {}
                for event in records:
                    if event["key"] in tracked:
                        first.setdefault(event["key"], event["time"])
            side.append({"hero": hid, "role": role if type(role) is int and 1 <= role <= 5 else None,
                         "first": first})
        teams.append(side)
    return {"id": raw["match_id"], "end": raw["start_time"] + raw["duration"],
            "duration": raw["duration"], "patch": raw["patch"], "teams": teams, "fights": _fight_cases(raw)}


def fit_purchase_reference(raw_matches, model, tracked):
    """Use the exact training side of the chronological pro split, including all five roles."""
    train_ids = set(model["coverage"]["train_match_ids"])
    games = {}
    for raw in raw_matches:
        if not isinstance(raw, dict) or raw.get("match_id") not in train_ids or raw["match_id"] in games:
            continue
        if (any(raw.get(k) for k in ("demo", "synthetic", "is_demo")) or raw.get("lobby_type") != 1
                or (raw.get("league") or {}).get("tier") not in ("professional", "premium")
                or not raw.get("leagueid") or any(type(raw.get(k)) is not int or raw[k] <= 0
                                                   for k in ("start_time", "duration", "patch"))):
            continue
        game = _training_game(raw, tracked)
        if game:
            games[game["id"]] = game
    if not games:
        raise ValueError("Нет подходящих карт из обучающей части про-выборки.")
    ordered = sorted(games.values(), key=lambda g: (g["end"], g["id"]))
    roles = Counter(p["role"] for g in ordered for side in g["teams"] for p in side
                    if p["role"] and p["first"] is not None)
    return {"format": "pro-fight-purchases-v1", "roles": {str(k): v for k, v in sorted(roles.items())},
            "tracked_items": sorted(tracked), "games": ordered}


def fit_ranked_purchase_reference(raw_matches, observations, policy, tracked):
    """Match full postgame logs to their audited OpenDota spectator observations."""
    games = {}
    for raw in raw_matches:
        mid = raw.get("match_id")
        if mid not in observations:
            continue
        cohort, _ = reference_eligibility(raw, policy, public_match=observations[mid])
        if cohort != "ranked_7300" or type(raw.get("patch")) is not int:
            continue
        game = _training_game(raw, tracked)
        if game and any(p["first"] is not None for side in game["teams"] for p in side):
            if mid in games and games[mid] != game:
                raise ValueError(f"Расходятся журналы покупок матча {mid}.")
            games[mid] = game
    if not games:
        raise ValueError("Нет подтверждённых рейтинговых карт с журналами покупок.")
    return {"format": "ranked-7300-fight-purchases-v1", "policy": policy,
            "tracked_items": sorted(tracked), "games": sorted(games.values(), key=lambda g: (g["end"], g["id"]))}


def write_reference(model, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        payload = json.dumps(model, ensure_ascii=False, separators=(",", ":")).encode()
        temporary.write_bytes(gzip.compress(payload, mtime=0))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class PurchaseIndex:
    def __init__(self, data):
        formats = {"pro-fight-purchases-v1": "professional", "ranked-7300-fight-purchases-v1": "ranked_7300"}
        if not isinstance(data, dict) or data.get("format") not in formats:
            raise ValueError("Неизвестный формат базы покупок.")
        self.cohort = formats[data["format"]]
        self.policy = data.get("policy", {})
        self.starts = {g["id"]: g["end"] - g["duration"] for g in data["games"]}
        self.by_role = defaultdict(list)
        self.encounters = defaultdict(list)
        for game in data["games"]:
            for side, players in enumerate(game["teams"]):
                enemies = frozenset(p["hero"] for p in game["teams"][1 - side])
                for player in players:
                    if player["role"] and player["first"] is not None:
                        self.by_role[game["patch"], player["role"]].append(
                            (game["id"], game["end"], game["duration"], player["hero"], enemies, player["first"]))
            roster = game["teams"][0] + game["teams"][1]
            for fight in game["fights"]:
                actors = fight["actors"]
                for side in range(2):
                    threats = set()
                    for i in range((1-side)*5, (2-side)*5):
                        actor = actors[i]
                        if actor:
                            keys = [k for k in actor["abilities"] or [] if k in SPELL_LOOKUP]
                            keys += [k for k in actor["items"] or [] if k in ITEM_THREATS]
                            threats.update((roster[i]["hero"], key) for key in keys)
                    for i in range(side*5, side*5+5):
                        player, actor = roster[i], actors[i]
                        if not actor or not player["role"]:
                            continue
                        record = (game["id"], game["end"], fight["start"], player["hero"], player["first"], actor)
                        for hero, key in threats:
                            self.encounters[game["patch"], player["role"], hero, key].append(record)

    @classmethod
    def load(cls, path=None):
        path = Path(path or REFERENCE).resolve()
        stat = path.stat()
        return _load_index(path, stat.st_mtime_ns, stat.st_size)

    @classmethod
    def for_session(cls, reference, model, supplied=None):
        cohort = reference.get("cohort")
        if cohort not in ("professional", "ranked_7300") or not reference.get("patch"):
            return None
        index = supplied or cls.load(RANKED_REFERENCE if cohort == "ranked_7300" else REFERENCE)
        if index.cohort != cohort:
            return None
        if cohort == "ranked_7300":
            if any(index.policy.get(k) != model["policy"][k] for k in ("patch", "patch_start")):
                return None
        return index

    def rates(self, row, role, second, enemy_ids, item_keys):
        """First logged purchase strictly before the fight; one vote per player appearance."""
        started = row.get("start_time")
        if not enemy_ids or not started or second < 0:
            return {}
        usable = []
        for mid, end, duration, hero, opponents, first in self.by_role.get((row.get("patch"), role), ()):
            if (mid != row["match_id"] and end < started and duration >= second and opponents & enemy_ids
                    and self.starts[mid] >= row.get("reference_patch_start", 0)):
                usable.append((mid, hero, first))
        hero_cases = [case for case in usable if case[1] == row["hero_id"]]
        same_hero = len(hero_cases) >= 8
        sample = hero_cases if same_hero else usable
        if len(sample) < 5:
            return {}
        result = {}
        for key in item_keys:
            hits = [(mid, first[key]) for mid, _, first in sample if key in first and first[key] < second]
            result[key] = {"n": len(sample), "bought": len(hits), "same_hero": same_hero,
                           "median": median(t for _, t in hits) if hits else None,
                           "example": hits[-1][0] if hits else None}
        return result

    def fight_actions(self, row, role, fight, threat, items):
        key = (row.get("patch"), role, threat["hero_id"], threat["key"])
        nearby = [r for r in self.encounters.get(key, ()) if r[0] != row["match_id"]
                  and self.starts[r[0]] >= row.get("reference_patch_start", 0)
                  and r[1] < (row.get("start_time") or 0) and abs(r[2] - fight["start"]) <= 300]
        prefix = row["detail"]["fight_review"].get("hero_prefix") or ""
        peers = [r for r in nearby if r[3] == row["hero_id"] and r[5]["abilities"] is not None]
        spells = Counter(k for r in peers for k in r[5]["abilities"] if prefix and k.startswith(prefix + "_"))
        if len(peers) >= 5 and len({r[0] for r in peers}) >= 3 and spells:
            ability, count = spells.most_common(1)[0]
            example = next(r for r in reversed(peers) if ability in r[5]["abilities"])
            name = next((name for _, name, owner in SPELL_LOOKUP.get(ability, ()) if owner == row["hero_id"]),
                        _spell_name(ability, prefix))
            fight.setdefault("pro_action", {"ability": name, "count": count,
                "n": len(peers), "enemy": threat["hero"], "threat": threat["ability"],
                "example": example[0], "example_start": example[2]})
        for item in items:
            k = item["key"]
            # Linken's active does not count passive blocks. Combined ward
            # dispensers do not identify which type was planted in item_uses.
            if k in ("sphere", "ward_sentry"):
                continue
            bought = [r for r in nearby if r[4] is not None and k in r[4] and r[4][k] < r[2]
                      and r[5]["items"] is not None]
            if len(bought) < 5 or len({r[0] for r in bought}) < 3:
                continue
            used = sum(k in r[5]["items"] for r in bought)
            item["fight_pro"] = {"uses": used, "n": len(bought), "enemy": threat["hero"], "threat": threat["ability"]}


@lru_cache(maxsize=2)
def _load_index(path, modified, size):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return PurchaseIndex(json.load(stream))


def add_purchase_evidence(fight, row, position, index):
    if index is None:
        return
    fight["peer_cohort"] = index.cohort
    groups = defaultdict(list)
    for item in fight["items"]:
        groups[item["category"]].append(item)
    for threat in fight["threats"]:
        matching_items = [i for i in groups[threat["category"]] if i["hero_id"] == threat["hero_id"]
                          and i["ability"] == threat["ability"]]
        index.fight_actions(row, position, fight, threat, matching_items)
    for item in fight["items"]:
        rates = index.rates(row, position, fight["start"], {item["hero_id"]}, [item["key"]])
        if item["key"] in rates:
            item["pro"] = {**rates[item["key"]], "opponents": [item["threat_hero"]]}
