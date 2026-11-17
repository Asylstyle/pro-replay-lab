"""Score frozen economy models on new, verified OpenDota ranked matches."""

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from pro_replay.coach_model import TARGETS, save_coach_model
from pro_replay.ml_experiments import _metrics, _records, canonical_hash, paired_bootstrap
from pro_replay.patch_reference import _select_games
from pro_replay.reference_policy import LIVE_SOURCE, reference_eligibility


# Timestamp of the commit containing the published all-data refit.
RELEASE = "bf67208c5888c239fbfd9005c63a2a5da1e5b590"
RELEASE_TIME = 1794940015
FROZEN = {
    "patch-reference-model.json": "2da6076ec827602168d85ac51a53a9dac1c8814696848adae851a1301a21fc3e",
    "ranked-client-model.json": "584a6bcc85e2bdcb97ef666f7b7be08faf5b6c553ecb05544255a12b3a44ec19",
}
FORMAT = "fresh-ranked-economy-evaluation-1"


def read_json(path):
    return json.loads(path.read_text(encoding="utf8"))


def published(examples):
    models = []
    for name, expected in FROZEN.items():
        model = read_json(examples / name)
        if canonical_hash(model) != expected:
            raise ValueError(f"{name} differs from frozen release {RELEASE}.")
        models.append(model)
    return tuple(models)


def seen_ids(examples, patch, previous, prior_studies):
    # Include training, model selection and earlier scored outcomes. A lobby
    # merely observed by /live or awaiting its first parsed card remains eligible.
    cohorts = read_json(examples / "coach-ml-holdout-manifest.json")["cohorts"]
    ranked = read_json(examples / "coach-ranked-evaluation.json")
    groups = [
        *patch["cohort_ids"].values(), patch["fit"]["train_match_ids"],
        previous["coverage"]["train_match_ids"], previous["coverage"]["test_match_ids"],
        previous["ranked_fit"]["train_match_ids"], previous["ranked_development_match_ids"],
        previous["fresh_test_match_ids"], ranked["all_ranked"]["match_ids"],
        read_json(examples / "ranked-domain-selection.json")["development_match_ids"],
        read_json(examples / "economy-protocol.json")["development_match_ids"],
        read_json(examples / "coach-ml-ranked-retrospective-manifest.json")["match_ids"],
        read_json(examples / "coach-ml-ranked-final-manifest.json")["match_ids"],
        read_json(examples / "pro-coach-candidate-v2.json")["fresh_test_match_ids"],
        *(c["match_ids"] for c in cohorts.values()),
    ]
    for path in prior_studies:
        study = read_json(path)
        if study.get("format") != FORMAT:
            raise ValueError(f"Unrecognized earlier fresh evaluation: {path}")
        groups.append(study["evaluation"]["unseen"]["match_ids"])
    return set().union(*groups)


def read_capture(snapshot, manifest):
    receipt = read_json(manifest)
    data = snapshot.read_bytes()
    if receipt["source"] != LIVE_SOURCE or hashlib.sha256(data).hexdigest() != receipt["snapshot_sha256"]:
        raise ValueError("Capture source or snapshot checksum differs from its manifest.")
    cards = json.loads(gzip.decompress(data))["matches"]
    ids = [c["match_id"] for c in cards]
    if len(set(ids)) != len(ids) or ids != receipt["matches"] or len(ids) != receipt["included"]:
        raise ValueError("Capture IDs must be unique and agree with the manifest.")
    observations = {row["match_id"]: row for row in receipt["observations"]}
    if len(observations) != len(receipt["observations"]):
        raise ValueError("Capture contains duplicate observation IDs.")
    included = {mid for mid, row in observations.items() if row["status"] == "included"}
    if included != set(ids) or any(observations[c["match_id"]]["evidence"] != c.get("opendota_live") for c in cards):
        raise ValueError("Manifest observations differ from accepted cards.")
    return cards, receipt


def summarize(games, records, mmr):
    ids = {g["match_id"] for g in games}
    rows = {k: [row for row in v if row["match_id"] in ids] for k, v in records.items()}
    return {
        "matches": len(games), "players": len(rows["patch"]), "match_ids": sorted(ids),
        "start_range": [min(g["start"] for g in games), max(g["start"] for g in games)] if games else None,
        "mmr_range": [min(mmr[mid] for mid in ids), max(mmr[mid] for mid in ids)] if ids else None,
        "metrics": {k: _metrics(v) for k, v in rows.items()},
        "patch_minus_previous": paired_bootstrap(rows["patch"], rows["previous"]),
    }


def diagnostics(records):
    roles = {}
    for key in [1, 2, 3, 4, 5, None]:
        samples = {k: [r for r in records[k] if r["reported_position"] == key] for k in ("patch", "previous")}
        if not samples["patch"]:
            continue
        roles[str(key) if key else "unknown"] = {
            "matches": len({r["match_id"] for r in samples["patch"]}), "players": len(samples["patch"]),
            "metrics": {k: _metrics(v) for k, v in samples.items()},
        }
    # Both predictions have the same deterministic match and player order.
    worst = {}
    for target in ("gold", "xp"):
        if records["patch"]:
            idx = max(range(len(records["patch"])), key=lambda i: abs(records["patch"][i]["errors"][target]))
            row = records["patch"][idx]
            worst[target] = {"match_id": row["match_id"], "hero_id": row["hero"],
                             "position_est": row["reported_position"], "observed_at_10": row["at10"][target],
                             "actual_at_20": row["actual"][target], "patch_prediction": row["predicted"][target],
                             "previous_prediction": records["previous"][idx]["predicted"][target]}
    return {"by_estimated_position": roles, "largest_patch_absolute_error": worst}


def evaluate(cards, policy, patch, previous, known):
    reference_eligibility(None, policy)  # Validate captured policy even for an empty export.
    if any(policy[key] != patch["policy"][key] for key in ("patch", "patch_start", "minimum_avg_mmr")):
        raise ValueError("The collection patch must match the published model patch.")
    selected, excluded = _select_games(cards, policy)
    games = []
    counts = Counter(excluded)
    for game in selected:
        if game["cohort"] != "ranked_7300":
            counts["outside_ranked_7300"] += 1
        elif game["match_id"] in known:
            counts["previously_used"] += 1
        else:
            games.append(game)
    models = {"patch": patch["fit"], "previous": previous["ranked_fit"],
              "rank_median": {"estimator": {"kind": "rank_median"}, "baseline": patch["fit"]["baseline"]}}
    records = {k: _records(fit, games) for k, fit in models.items()}
    eligible_ids = {g["match_id"] for g in games}
    mmr = {c["match_id"]: c["opendota_live"]["average_mmr"] for c in cards if c["match_id"] in eligible_ids}
    last_end = max(patch["fit"]["train_last_end"], previous["ranked_fit"]["train_last_end"])
    windows = {"unseen": games, "after_training": [g for g in games if g["start"] > last_end],
               "after_release": [g for g in games if g["start"] > max(last_end, RELEASE_TIME)]}
    return {"format": FORMAT, "frozen_commit": RELEASE, "frozen_commit_time": RELEASE_TIME,
            "models_sha256_canonical_json": FROZEN, "latest_fitting_match_end": last_end, "policy": policy,
            "excluded": dict(counts), "previously_used_ids_sha256": canonical_hash(sorted(known)),
            "previously_used_ids_count": len(known),
            "evaluation": {name: summarize(subset, records, mmr) for name, subset in windows.items()},
            "diagnostics_unseen": diagnostics(records),
            "interpretation": "Predictions use data through 10:00; targets are cumulative at 20:00. "
            "Bias is prediction minus observation. Negative paired MAE differences favor the patch refit. "
            "The three windows overlap: only after_release requires match start after the frozen commit. "
            "Position estimates are postgame diagnostics only. Intervals are per target and resample whole games."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--exclude-study", action="append", default=[], type=Path,
                        help="Exclude match IDs scored in a previous fresh-ranked evaluation. Repeatable.")
    args = parser.parse_args()
    examples = REPO / "examples"
    patch, previous = published(examples)
    cards, receipt = read_capture(args.snapshot, args.manifest)
    known = seen_ids(examples, patch, previous, args.exclude_study)
    result = evaluate(cards, receipt["policy"], patch, previous, known)
    result["capture_snapshot_sha256"] = receipt["snapshot_sha256"]
    result["capture_manifest_sha256"] = canonical_hash(receipt)
    save_coach_model(result, args.out)
    for key, group in result["evaluation"].items():
        print(key, group["matches"], {t: group["metrics"]["patch"][t]["mae"] for t in TARGETS})


if __name__ == "__main__":
    main()
