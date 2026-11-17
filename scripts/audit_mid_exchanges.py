"""Check published mid decisions against their exact replay frames."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pro_replay.coaching import json_file
from pro_replay.mid_conditional import conditional_keys
from pro_replay.mid_matchup import is_mid_laner, mid_matchup_review
from pro_replay.mid_spells import spell_catalog
from pro_replay.mid_windows import mid_decision_windows
from pro_replay.replay_files import DECODER, replay_evidence


def effect(action):
    return {"button": action["key"], "damage": [action["damage_min"], action["damage_max"]]}


def audit_view(raw, player, timeline):
    slot = player["player_slot"]
    pair = mid_matchup_review(raw, slot)
    if not pair or pair["status"] != "available":
        raise ValueError(f"No verified mid matchup for {raw['match_id']} / {slot}")
    result = mid_decision_windows(raw, slot, pair, timeline)
    compact = []
    for c in result["exchange_decisions"]:
        t = c["time"]
        frame, = timeline["frame_window"](t, t)
        enemy = pair["opponent"]["slot"]
        actor, rival = (frame["heroes"][str(idx)] for idx in (slot, enemy))
        team = 2 if slot < 128 else 3
        assert frame["tick"] == c["tick"] and actor["alive"] and rival["alive"]
        assert actor["hero_id"] == player["hero_id"] and rival["hero_id"] == pair["opponent"]["id"]
        assert rival["visible_to"][str(team)] and actor["visible_to"][str(5 - team)]
        entry = {"second": t, "choice": c["action"], "our_level": actor["level"], "enemy_level": rival["level"]}
        own, reply, creep = c["own"], c["reply"], c["creep"]
        if own:
            entry["hero_option"] = effect(own)
        if reply["best"]:
            entry["enemy_option"] = effect(reply["best"])
        if reply["controls"]:
            entry["enemy_control"] = reply["controls"]
        if reply["blocked"]:
            entry["blocked_replies"] = [
                {**b, "cooldown": round(b["cooldown"], 2)} if "cooldown" in b else b for b in reply["blocked"]]
        if c["caution"]:
            entry["uncertainty"] = c["caution"]
        if c.get("prior_hits"):
            entry["prior_hits"] = c["prior_hits"]
        if c["margin_pp"] is not None:
            entry["health_margin_pp"] = c["margin_pp"]
        if creep:
            target, = [v for v in frame["ranged_creeps"] if v["id"] == creep["id"]]
            assert target["team"] == 5 - team and target["visible_to"][str(team)]
            assert target["hp"] == creep["hp"] and creep["damage_min"] >= target["hp"]
            entry["creep_option"] = {**effect(creep), "hp":target["hp"], "same_area":creep["joint_hero"]}
        compact.append(entry)
    return {"slot":slot, "hero":pair["self"]["name"], "opponent":pair["opponent"]["name"],
            "seconds_sampled":result["seconds"], "decisions":compact,
            "observed_exchanges":result["observed_exchanges"]}


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("matches", type=Path, nargs="+", help="OpenDota match cards (.json or .json.gz)")
    args.add_argument("--cache", type=Path, required=True, help="Valve replay cache")
    args.add_argument("--output", type=Path, required=True)
    options = args.parse_args()
    seen, matches, counts = set(), [], Counter()
    for path in options.matches:
        raw = json_file(path)
        mid = raw["match_id"]
        if mid in seen:
            continue
        seen.add(mid)
        mids = [p for p in raw["players"] if is_mid_laner(p) and not p.get("is_roaming")]
        if len(mids) != 2:
            raise ValueError(f"{mid} requires two identified lane opponents")
        with replay_evidence(raw, options.cache) as (timeline, message):
            if timeline is None:
                raise ValueError(f"{mid}: {message}")
            players = [audit_view(raw, p, timeline) for p in sorted(mids, key=lambda p: p["player_slot"])]
        for player in players:
            counts.update(c["choice"] for c in player["decisions"])
        matches.append({"match_id":mid, "source":f"https://www.opendota.com/matches/{mid}", "players":players})
        print(mid, [p["hero"] for p in players], flush=True)
    book = spell_catalog()
    specific_heroes = set()
    specific_buttons = set()
    for hid, hero in book["heroes"].items():
        keys = {s["key"] for s in hero["abilities"] if s["proof"]} | set(conditional_keys(int(hid)))
        if keys:
            specific_heroes.add(hid)
            specific_buttons.update(keys)
    coverage = {**book["coverage"], "distinct_modeled_buttons": len(specific_buttons),
                "heroes_with_modeled_buttons": len(specific_heroes)}
    data = {"patch": book["patch"], "catalog": coverage,
            "reader_sha256":json_file(DECODER / "runtime.json")["reader_sha256"],
            "scope":"Selected displayed cards from the first ten minutes. Decisions are estimates; observed_exchanges sums actual attributed damage over at most three seconds. Counts are not measures of accuracy.",
            "decision_counts":dict(counts), "matches":matches}
    options.output.write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n",encoding="utf8")


if __name__ == "__main__":
    main()
