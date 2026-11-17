"""Rebuild the frozen ML datasets from cached OpenDota responses.

Optional downloading uses the project's bounded-retry, rate-limited client.
Examples:
    python scripts/rebuild_ml_snapshots.py --dataset training --raw-dir data/ml-raw
    python scripts/rebuild_ml_snapshots.py --dataset holdout --fetch-missing

The published holdout manifest fixes the IDs and selection policy. This command
does not select replacement matches or inspect predictions. Retaining the raw
cache is necessary for bit-for-bit reproduction if upstream data later changes.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from pro_replay.coach_model import _game
from pro_replay.source import OpenDota


def compact(raw):
    """Keep roster, minute 0-10 trajectories and minute 20 economy labels.

    Duration, position_est and team identifiers are eligibility/audit metadata;
    the feature extractor must never use final positions or future trajectories.
    """
    result = {key: raw.get(key) for key in (
        "match_id", "start_time", "duration", "leagueid", "lobby_type",
        "game_mode", "patch", "version", "human_players", "radiant_team_id",
        "dire_team_id",
    )}
    result["league"] = {key: (raw.get("league") or {}).get(key)
                        for key in ("leagueid", "name", "tier")}
    result["players"] = []
    for player in raw["players"]:
        row = {key: player.get(key) for key in (
            "player_slot", "hero_id", "position_est", "leaver_status",
        )}
        times = player.get("times") or []
        indices = [index for index, second in enumerate(times)
                   if isinstance(second, (int, float))
                   and (0 <= second <= 600 or second == 1200)]
        row["times"] = [times[index] for index in indices]
        for key in ("lh_t", "gold_t", "xp_t", "dn_t", "networth_t"):
            values = player.get(key)
            if isinstance(values, list):
                row[key] = [values[index] if index < len(values) else None
                            for index in indices]
        result["players"].append(row)
    return result


def load_raw(match_id, raw_dir, api):
    path = raw_dir / f"{match_id}.json.gz"
    if not path.exists():
        if api is None:
            raise FileNotFoundError(f"Missing {path}; use --fetch-missing to download")
        raw = api.match(match_id)
        raw_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(raw, separators=(",", ":")).encode(),
                                       compresslevel=2, mtime=0))
        return raw
    with gzip.open(path, "rt", encoding="utf8") as source:
        raw = json.load(source)
    if raw.get("match_id") != match_id:
        raise ValueError(f"Cached response has wrong match ID: {path}")
    return raw


def rebuild(dataset, raw_dir, api):
    if dataset == "training":
        ids = list(map(int, (REPO / "examples/coach-pro-match-ids.txt").read_text().split()))
        rows = []
        for match_id in ids:
            raw = load_raw(match_id, raw_dir, api)
            _, reason = _game(raw)
            if reason:
                raise ValueError(f"Original training match {match_id}: {reason}")
            rows.append(compact(raw))
        return {
            "schema": "pro-replay-ml-snapshots-v1",
            "provenance": "Original500professionalmatches; first400 temporaldevelopment plus previouslyinspected100test. This corpus is not a new sealed test set.",
            "matches": rows,
        }
    if dataset == "legacy-ranked":
        manifest = json.loads((REPO / "examples/coach-ml-legacy-ranked-manifest.json").read_text(encoding="utf8"))
        rows = []
        for source in manifest["sources"]:
            raw = load_raw(source["match_id"], raw_dir, api)
            row = compact(raw)
            row["source_avg_rank_tier"] = source["avg_rank_tier"]
            rows.append(row)
        return {"schema": "pro-replay-ml-snapshots-v1",
                "provenance": manifest["provenance"], "matches": rows}
    if dataset == "retrospective-ranked":
        manifest = json.loads((REPO / "examples/coach-ml-ranked-retrospective-manifest.json").read_text(encoding="utf8"))
        policy = manifest["sampling_policy"]
        excluded = set(policy["excluded_prior_match_ids"])
        rank_by_id = {int(row["match_id"]): row.get("avg_rank_tier")
                      for page in manifest["candidate_pages"]
                      for row in page["ranked_candidates"]}
        rows = []
        for match_id in manifest["match_ids"]:
            raw = load_raw(match_id, raw_dir, api)
            _, reason = _game(raw, ranked=True)
            if reason:
                raise ValueError(f"Retrospective match {match_id}: {reason}")
            if match_id in excluded:
                raise ValueError(f"Retrospective match {match_id} overlaps development data")
            if not policy["window_start_inclusive"] <= raw["start_time"] < policy["window_end_exclusive"]:
                raise ValueError(f"Retrospective match {match_id} is outside the fixed window")
            if raw.get("human_players") != 10 or any(player.get("leaver_status") != 0 for player in raw["players"]):
                raise ValueError(f"Retrospective match {match_id} violates roster eligibility")
            row = compact(raw)
            row["source_avg_rank_tier"] = rank_by_id.get(match_id)
            rows.append(row)
        return {"schema": "pro-replay-ml-snapshots-v1",
                "selection_policy_sha256": manifest["sampling_policy_sha256"],
                "provenance": policy["interpretation"], "ranked": rows}
    manifest = json.loads((REPO / "examples/coach-ml-holdout-manifest.json").read_text(encoding="utf8"))
    result = {"schema": "pro-replay-ml-snapshots-v1",
              "selection_policy_sha256": manifest["sampling_policy_sha256"]}
    rank_by_id = {
        int(row["match_id"]): row.get("avg_rank_tier")
        for page in manifest["candidate_pages"]
        for row in page.get("eligible_lobby_candidates", [])
    }
    for cohort in ("professional", "ranked"):
        rows = []
        for match_id in manifest["cohorts"][cohort]["match_ids"]:
            raw = load_raw(match_id, raw_dir, api)
            _, reason = _game(raw, ranked=cohort == "ranked")
            if reason:
                raise ValueError(f"Frozen holdout match {match_id}: {reason}")
            if raw["start_time"] <= manifest["sampling_policy"]["strict_start_after"]:
                raise ValueError(f"Frozen holdout match {match_id} is not strictly future")
            if any(player.get("leaver_status") != 0 for player in raw["players"]):
                raise ValueError(f"Frozen holdout match {match_id} has a recorded disconnect")
            row = compact(raw)
            row["source_avg_rank_tier"] = rank_by_id.get(match_id)
            rows.append(row)
        result[cohort] = rows
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=("training", "holdout", "legacy-ranked", "retrospective-ranked"), required=True)
    parser.add_argument("--raw-dir", type=Path, default=REPO / "data/ml-raw")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fetch-missing", action="store_true")
    args = parser.parse_args()
    output = args.output or REPO / "data" / f"coach-ml-{args.dataset}-snapshots.json.gz"
    payload = rebuild(args.dataset, args.raw_dir, OpenDota() if args.fetch_missing else None)
    encoded = gzip.compress(json.dumps(payload, separators=(",", ":")).encode(),
                            compresslevel=9, mtime=0)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(encoded)
    print(json.dumps({"output": str(output), "sha256": hashlib.sha256(encoded).hexdigest(),
                      "matches": len(payload.get("matches", []))
                      or len(payload.get("professional", [])) + len(payload.get("ranked", []))}))


if __name__ == "__main__":
    main()
