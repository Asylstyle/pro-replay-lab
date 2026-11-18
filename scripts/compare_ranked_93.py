"""Compare the frozen 93-pub and 53-pub economy models on the same unseen matches."""

import argparse
from collections import Counter
from datetime import datetime
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
LATE_ROSTER_SHA256 = "3a724b8ba9e5d995646807e511545930722d69c8a76ea8c4e958bc6497959a0e"
PENDING = {"awaiting_postgame", "incomplete_roster_or_snapshots", "incomplete_ten_human_roster",
           "match_unfinished_at_cutoff", "missing_match_metadata", "network_failure"}


def checked_fits(old, new, protocol, policy, outcome_deadline):
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
            or not end <= frozen <= deadline or policy["as_of"] > outcome_deadline):
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


def late_outcome_window(cards, receipt, roster, original, protocol, known_ids):
    """Join later results only to pending lobby observations fixed before outcomes were inspected."""
    if roster.get("format") != "ranked-93-late-roster-v1" or canonical_hash(roster) != LATE_ROSTER_SHA256:
        raise ValueError("Late roster differs from its registered digest.")
    if (original is None or roster["protocol_sha256"] != canonical_hash(protocol)
            or roster["source_manifest_sha256"] != canonical_hash(original)
            or roster["source_snapshot_sha256"] != original["snapshot_sha256"]):
        raise ValueError("Late roster does not match the original protocol and capture.")
    original_deadline = protocol["collection"]["deadline_utc_epoch"]
    policy = receipt["policy"]
    if (roster["original_deadline"] != original_deadline or original["policy"]["as_of"] > original_deadline
            or not original_deadline <= roster["enrolled_at"] <= policy["as_of"] <= roster["outcome_deadline"]
            or {k: v for k, v in policy.items() if k != "as_of"} !=
               {k: v for k, v in original["policy"].items() if k != "as_of"}):
        raise ValueError("Observation window, outcome window or patch policy differs from registration.")
    enrolled = {r["match_id"]: r["evidence"] for r in original["observations"]
                if r["status"] in PENDING and r["match_id"] not in known_ids}
    if roster["match_ids"] != sorted(enrolled):
        raise ValueError("Late roster must contain every initially unresolved non-training match.")
    rows = receipt["observations"]
    if len(rows) != len(enrolled) or {r["match_id"] for r in rows} != set(enrolled):
        raise ValueError("Late ledger must account for every enrolled lobby and no others.")
    for row in rows:
        evidence = enrolled[row["match_id"]]
        if row["evidence"] != evidence:
            raise ValueError("Late ledger changed the original OpenDota Live evidence.")
        observed = datetime.fromisoformat(evidence["observed_at_utc"].replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None or observed.timestamp() > original_deadline:
            raise ValueError("A late lobby observation falls outside the original observation window.")
    confirmed = {row["match_id"] for row in rows if row["status"] == "included"}
    if ({c["match_id"] for c in cards} != confirmed or
            any(c.get("opendota_live") != enrolled[c["match_id"]] for c in cards)):
        raise ValueError("Each late card must carry the enrolled lobby's unchanged evidence.")
    return {"roster_sha256": LATE_ROSTER_SHA256, "roster_match_count": len(enrolled),
            "source_manifest_sha256": roster["source_manifest_sha256"],
            "original_capture_as_of": original["policy"]["as_of"], "original_observation_deadline": original_deadline,
            "roster_enrolled_at": roster["enrolled_at"], "registered_outcome_deadline": roster["outcome_deadline"],
            "outcome_capture_as_of": policy["as_of"], "outcome_status_counts": dict(Counter(r["status"] for r in rows))}


def compare(cards, receipt, old, new, protocol, prior_ids, *, late_roster=None, original_receipt=None):
    policy = receipt["policy"]
    training_ids = set().union(*old["cohort_ids"].values(), *new["cohort_ids"].values())
    deadline = protocol["collection"]["deadline_utc_epoch"]
    followup = None
    if late_roster is not None:
        followup = late_outcome_window(cards, receipt, late_roster, original_receipt, protocol, training_ids | prior_ids)
        deadline = followup["registered_outcome_deadline"]
    fits = checked_fits(old, new, protocol, policy, deadline)
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
    result = {"format": "ranked-93-vs-53-evaluation-v1", "protocol_sha256": canonical_hash(protocol),
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
    if followup:
        result["format"] = "ranked-93-vs-53-late-outcomes-v1"
        result["late_outcomes"] = followup
        result["interpretation"] = ("Separate delayed-outcome follow-up to the deadline evaluation. "
            "Only lobbies previously enrolled from the frozen original observation ledger can enter. "
            + result["interpretation"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=STUDY / "protocol.json")
    parser.add_argument("--capture", type=Path, default=STUDY / "test")
    parser.add_argument("--old-model", type=Path, default=EXAMPLES / "patch-cohort-model.json")
    parser.add_argument("--new-model", type=Path, default=STUDY / "model.json")
    parser.add_argument("--late-roster", type=Path, help="Use only the registered lobbies and later outcome window")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cards, receipt = read_capture(args.capture / "matches.json.gz", args.capture / "manifest.json")
    old, new = load_reference(args.old_model), load_reference(args.new_model)
    prior = historical_ids()
    prior.update(read_json(EXAMPLES / "ranked-collection-2026-11-18/evaluation.json")["evaluation"]["unseen"]["match_ids"])
    options = {}
    if args.late_roster is not None:
        if args.out.resolve() in {(STUDY / "evaluation.json").resolve(), (STUDY / "test/evaluation.json").resolve()}:
            raise ValueError("Keep the delayed-outcome report separate from the original deadline evaluation.")
        _, original = read_capture(STUDY / "test/matches.json.gz", STUDY / "test/manifest.json")
        options = {"late_roster": read_json(args.late_roster), "original_receipt": original}
    report = compare(cards, receipt, old, new, read_json(args.protocol), prior, **options)
    save_coach_model(report, args.out)
    primary = report["evaluation"]["primary"]
    print(f'Primary: {primary["matches"]} matches / {primary["players"]} players')
    for key in ("gold", "xp"):
        old_error = primary["metrics"]["old_53"][key]["mae"]
        new_error = primary["metrics"]["new_93"][key]["mae"]
        print(f'{key} MAE: old={old_error}, new={new_error}')


if __name__ == "__main__":
    main()
