"""Compare the frozen 93-pub and 53-pub economy models on the same unseen matches."""

import argparse
from collections import Counter
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pro_replay.coach_model import save_coach_model
from pro_replay.ml_experiments import _metrics, _records, canonical_hash, paired_bootstrap
from pro_replay.patch_reference import EXAMPLES, _select_games, load_reference
from pro_replay.reference_policy import reference_eligibility
from scripts.evaluate_fresh_ranked import read_capture, read_json
from scripts.score_ranked_collection import historical_ids


STUDY = EXAMPLES / "ranked-93-candidate"
RANKED = "ranked_7300"


def checked_fits(old, new, protocol, policy):
    if protocol.get("format") != "ranked-93-comparison-protocol-v1":
        raise ValueError("Unknown comparison protocol.")
    reference_eligibility(None, policy)
    patch = (protocol["patch"], protocol["patch_start"], protocol["min_avg_mmr"])
    fits = {}
    for name, model, count in (("old", old, 53), ("new", new, 93)):
        spec = protocol["models"][name]
        if canonical_hash(model) != spec["canonical_sha256"]:
            raise ValueError(f"{name} model differs from the frozen weights.")
        fit = model["cohorts"][RANKED]["fit"]
        ids = fit["train_match_ids"]
        if (count != spec["ranked_count"] or len(ids) != count or len(set(ids)) != count
                or set(ids) != set(model["cohort_ids"][RANKED])
                or canonical_hash(sorted(ids)) != spec["ranked_ids_sha256"]):
            raise ValueError(f"{name} model's training IDs differ from the protocol.")
        for record in (model["policy"], policy):
            if (record["patch"], record["patch_start"], record["minimum_avg_mmr"]) != patch:
                raise ValueError("The models and test capture must cover the registered patch and MMR threshold.")
        fits[name] = fit
    if not set(fits["old"]["train_match_ids"]) < set(fits["new"]["train_match_ids"]):
        raise ValueError("The 93-pub fit must include every original training match.")
    end = protocol["latest_training_end"]
    frozen = protocol["frozen_at"]
    deadline = protocol["collection"]["deadline_utc_epoch"]
    if (end != fits["new"]["train_last_end"] or end < fits["old"]["train_last_end"]
            or not end <= frozen <= deadline or policy["as_of"] > deadline):
        raise ValueError("Training boundary, freeze or capture time disagrees with the protocol.")
    return fits


def summarize(games, fits, ratings, *, include_matches=False):
    old, new, details = [], [], []
    for game in games:
        old_rows = _records(fits["old"], [game])
        new_rows = _records(fits["new"], [game])
        old.extend(old_rows)
        new.extend(new_rows)
        if include_matches:
            mid = game["match_id"]
            entry = {"match_id": mid, "start_time": game["start"], "avg_mmr": ratings[mid]}
            for name, rows in (("old_53", old_rows), ("new_93", new_rows)):
                entry[name] = {k: v["mae"] for k, v in _metrics(rows).items() if k in ("gold", "xp")}
            details.append(entry)
    metrics = {"old_53": _metrics(old), "new_93": _metrics(new)}
    delta = paired_bootstrap(new, old)
    # Keep the sign consistent for unit and percentage differences.
    for key, estimate in delta["targets"].items():
        previous = metrics["old_53"][key]["mae"]
        estimate["percent_change_vs_old"] = (100 * estimate["mae_delta"] / previous if previous else None)
    ids = [g["match_id"] for g in games]
    result = {"matches": len(games), "players": len(new), "match_ids": ids,
              "start_range": [games[0]["start"], games[-1]["start"]] if games else None,
              "mmr_range": [min(ratings[i] for i in ids), max(ratings[i] for i in ids)] if ids else None,
              "metrics": metrics, "new_minus_old": delta}
    if include_matches:
        result["per_match"] = details
    return result


def compare(cards, receipt, old, new, protocol, prior_ids):
    policy = receipt["policy"]
    fits = checked_fits(old, new, protocol, policy)
    training_ids = set().union(*old["cohort_ids"].values(), *new["cohort_ids"].values())
    ids = [c["match_id"] for c in cards]
    if len(set(ids)) != len(ids):
        raise ValueError("The comparison requires unique match IDs in the capture.")
    games, excluded = _select_games(cards, policy)
    excluded = Counter(excluded)
    primary, overlap = [], []
    for game in games:
        mid = game["match_id"]
        if game["cohort"] != RANKED:
            excluded["outside_ranked_7300"] += 1
        elif mid in training_ids:
            excluded["in_model_training"] += 1
        elif mid in prior_ids:
            excluded["in_previous_study"] += 1
        elif game["start"] <= protocol["latest_training_end"]:
            overlap.append(game)
        else:
            primary.append(game)
    ratings = {c["match_id"]: c["opendota_live"]["average_mmr"] for c in cards if c.get("opendota_live")}
    after_freeze = [g for g in primary if g["start"] > protocol["frozen_at"]]
    return {"format": "ranked-93-vs-53-evaluation-v1", "protocol_sha256": canonical_hash(protocol),
            "models": protocol["models"], "policy": policy, "capture_snapshot_sha256": receipt["snapshot_sha256"],
            "capture_manifest_sha256": canonical_hash(receipt), "excluded": dict(excluded),
            "excluded_known_ids_sha256": canonical_hash(sorted(training_ids | prior_ids)),
            "latest_training_end": protocol["latest_training_end"], "frozen_at": protocol["frozen_at"],
            "primary_targets": ["gold", "xp"], "secondary_targets": ["lh"],
            "evaluation": {"primary": summarize(primary, fits, ratings, include_matches=True),
                           "after_freeze": summarize(after_freeze, fits, ratings),
                           "overlap_diagnostic": summarize(overlap, fits, ratings)},
            "interpretation": "Primary matches started strictly after the final training match ended. "
            "After-freeze matches are a subset of primary. Overlap diagnostics are disjoint from primary. "
            "Each prediction uses minute 10 to estimate the resource total at minute 20. Negative MAE "
            "deltas favor the 93-pub model. The paired 95% intervals resample whole matches and are "
            "unavailable with fewer than two matches. Small samples describe only the observed games."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=STUDY / "protocol.json")
    parser.add_argument("--capture", type=Path, default=STUDY / "test")
    parser.add_argument("--old-model", type=Path, default=EXAMPLES / "patch-cohort-model.json")
    parser.add_argument("--new-model", type=Path, default=STUDY / "model.json")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cards, receipt = read_capture(args.capture / "matches.json.gz", args.capture / "manifest.json")
    old, new = load_reference(args.old_model), load_reference(args.new_model)
    prior = historical_ids()
    prior.update(read_json(EXAMPLES / "ranked-collection-2026-11-18/evaluation.json")["evaluation"]["unseen"]["match_ids"])
    report = compare(cards, receipt, old, new, read_json(args.protocol), prior)
    save_coach_model(report, args.out)
    primary = report["evaluation"]["primary"]
    print(f'Primary: {primary["matches"]} matches / {primary["players"]} players')
    for key in ("gold", "xp"):
        old_error = primary["metrics"]["old_53"][key]["mae"]
        new_error = primary["metrics"]["new_93"][key]["mae"]
        print(f'{key} MAE: old={old_error}, new={new_error}')


if __name__ == "__main__":
    main()
