"""Score a verified ranked batch against the frozen 53-pub coach and its training median."""

import argparse
from collections import Counter
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pro_replay.coach_model import save_coach_model
from pro_replay.ml_experiments import _metrics, _records, canonical_hash, paired_bootstrap
from pro_replay.patch_reference import EXAMPLES, PACKAGED_MODEL, _select_games, load_reference
from pro_replay.reference_policy import reference_eligibility
from scripts.evaluate_fresh_ranked import published, read_capture, read_json, seen_ids


RELEASE = "66e994d1b491dced075957ce9860d0557cdd5eeb"
RELEASE_TIME = 1794954779
MODEL_HASH = "7affeba6620b3e5b051f69fe95583c319500397f69f095a7c5d10946e5417c49"


def historical_ids():
    legacy_patch, legacy_client = published(EXAMPLES)
    earlier_test = EXAMPLES / "fresh-ranked-2026-11-17/evaluation.json"
    ids = seen_ids(EXAMPLES, legacy_patch, legacy_client, [earlier_test])
    legacy = read_json(EXAMPLES / "coach-ml-legacy-ranked-manifest.json")
    ids.update(row["match_id"] for row in legacy["sources"])
    split = read_json(EXAMPLES / "item-model-comparison.json")["split"]
    ids.update(split["train_match_ids"], split["legacy_test_match_ids"])
    for name in ("mid-lane-replay-check.json", "mid-exchange-check.json"):
        ids.update(row["match_id"] for row in read_json(EXAMPLES / name)["matches"])
    return ids


def summarize(games, fit):
    median = {"estimator": {"kind": "rank_median"}, "baseline": fit["baseline"]}
    predictions = _records(fit, games)
    reference = _records(median, games)
    return {"matches": len(games), "players": len(predictions),
            "match_ids": [g["match_id"] for g in games],
            "metrics": {"ranked_model": _metrics(predictions), "rank_median": _metrics(reference)},
            "model_minus_median": paired_bootstrap(predictions, reference)}


def score(cards, receipt, model, prior_ids):
    if canonical_hash(model) != MODEL_HASH:
        raise ValueError(f"Model differs from the published weights at {RELEASE}")
    policy = receipt["policy"]
    reference_eligibility(None, policy)
    if any(policy[k] != model["policy"][k] for k in ("patch", "patch_start", "minimum_avg_mmr")):
        raise ValueError("Capture and model must use the same patch and MMR threshold.")
    fit = model["cohorts"]["ranked_7300"]["fit"]
    current_ids = set().union(*model["cohort_ids"].values())
    selected, excluded = _select_games(cards, policy)
    counts = Counter(excluded)
    unseen = []
    for game in selected:
        mid = game["match_id"]
        if game["cohort"] != "ranked_7300":
            counts["outside_ranked_7300"] += 1
        elif mid in current_ids:
            counts["in_current_reference"] += 1
        elif mid in prior_ids:
            counts["in_previous_study"] += 1
        else:
            unseen.append(game)
    last_end = fit["train_last_end"]
    windows = {"unseen": unseen,
               "through_training_end": [g for g in unseen if g["start"] <= last_end],
               "after_training_through_release": [g for g in unseen if last_end < g["start"] <= RELEASE_TIME],
               "after_release": [g for g in unseen if g["start"] > RELEASE_TIME]}
    return {"format": "ranked-cohort-collection-score-1", "model_commit": RELEASE,
            "model_commit_time_utc_epoch": RELEASE_TIME, "model_sha256_canonical_json": MODEL_HASH,
            "last_ranked_training_match_end": last_end, "ranked_training_matches": len(fit["train_match_ids"]),
            "capture_snapshot_sha256": receipt["snapshot_sha256"], "capture_manifest_sha256": canonical_hash(receipt),
            "policy": policy, "excluded": dict(counts), "known_match_ids_count": len(current_ids | prior_ids),
            "known_match_ids_sha256": canonical_hash(sorted(current_ids | prior_ids)),
            "evaluation": {name: summarize(group, fit) for name, group in windows.items()},
            "interpretation": "All unseen matches are split by start time into three disjoint groups. "
            "Only after_release started after publication. Predictions use minute 10 to estimate totals at minute 20. "
            "The median comparator uses resource gains by minute-10 farm rank from the same frozen ranked training games. "
            "Paired differences resample whole matches. Small samples only describe this collection."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path, help="Folder with matches.json.gz and manifest.json")
    parser.add_argument("--model", type=Path, default=EXAMPLES / PACKAGED_MODEL)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    cards, receipt = read_capture(args.capture / "matches.json.gz", args.capture / "manifest.json")
    report = score(cards, receipt, load_reference(args.model), historical_ids())
    save_coach_model(report, args.out)
    for name, group in report["evaluation"].items():
        print(f'{name}: {group["matches"]} matches, {group["players"]} player rows')


if __name__ == "__main__":
    main()
