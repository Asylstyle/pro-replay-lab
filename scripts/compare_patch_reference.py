"""Compare the selected patch recipe with the deployed coach on identical held-out games."""

import argparse
import gzip
import json
from math import isclose
import os
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from pro_replay.coach_model import TARGETS, save_coach_model
from pro_replay.ml_experiments import (_metrics, _records, canonical_hash, fit_candidate,
                                       paired_bootstrap)
from pro_replay.patch_reference import _select_games


def read(folder, name):
    path = folder / name
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as source:
        return json.load(source)


def compare(folder):
    ref = read(folder, "patch-reference-model.json")
    deployed = read(folder, "ranked-client-model.json")
    pro = read(folder, "patch-reference-pro.json.gz")["matches"]
    pubs = read(folder, "patch-reference-ranked.json.gz")["matches"]
    games, excluded = _select_games(pro + pubs, ref["policy"])
    if excluded or canonical_hash(games) != ref["source_data_hash"]:
        raise ValueError("Patch reference corpus differs from its published fit.")
    validation = ref["validation"]
    by_id = {g["match_id"]: g for g in games}
    train = [by_id[mid] for mid in validation["selection_train_ids"]]
    test = [by_id[mid] for mid in validation["holdout_ids"]]
    if not train or not test or max(g["end"] for g in train) > min(g["start"] for g in test):
        raise ValueError("Holdout must follow all completed fitting matches.")

    # Reconstruct only the evaluation-time fit. The published all-data refit includes the holdout.
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=1):
        fit = fit_candidate(validation["winner"], train)
        baseline = fit_candidate("rank_median", train)
    patch_records = _records(fit, test)
    baseline_records = _records(baseline, test)
    # The existing coach uses its original pro Ridge for verified tournaments.
    # The rich deployed predictor is routed only to Ranked All Pick records.
    legacy = {"estimator": {"kind": "legacy", "fit": deployed["fit"]}}
    deployed_records = []
    for g in test:
        routed_fit = deployed["ranked_fit"] if g["cohort"] == "ranked_7300" else legacy
        deployed_records.extend(_records(routed_fit, [g]))
    for key, metric in _metrics(patch_records).items():
        published = validation["holdout"]["all"][validation["winner"]][key]["mae"]
        if not isclose(metric["mae"], published, abs_tol=1e-7):
            raise ValueError(f"Reconstructed evaluation fit does not reproduce published {key} error.")

    previous = read(folder, "coach-ml-holdout-manifest.json")["cohorts"]
    old_ranked = read(folder, "coach-ranked-evaluation.json")
    seen_sets = {
        "deployed_pro_weight_training": set(deployed["coverage"]["train_match_ids"]),
        "earlier_100_pro_holdout": set(deployed["coverage"]["test_match_ids"]),
        "earlier_119_ranked_holdout": set(old_ranked["all_ranked"]["match_ids"]),
        "earlier_120_ranked_source_including_short": set(old_ranked["source_match_ids"]),
        "deployed_ranked_model_selection": set(deployed["ranked_development_match_ids"]),
        "prior_phase_one_pro_holdout": set(previous["professional"]["match_ids"]),
        "prior_phase_one_ranked_holdout": set(previous["ranked"]["match_ids"]),
        "deployed_final_ranked_holdout": set(deployed["fresh_test_match_ids"]),
    }
    previous_ids = set().union(*seen_sets.values())
    evaluation = {}
    for name, subset in [
        ("all", test),
        ("professional", [g for g in test if g["cohort"] == "professional"]),
        ("ranked_7300", [g for g in test if g["cohort"] == "ranked_7300"]),
        ("previously_unused_matches", [g for g in test if g["match_id"] not in previous_ids]),
    ]:
        ids = {g["match_id"] for g in subset}
        records = [[r for r in rows if r["match_id"] in ids]
                   for rows in (patch_records, deployed_records, baseline_records)]
        evaluation[name] = {
            "matches": len(ids), "players": len(records[0]), "match_ids": sorted(ids),
            "previous_overlap": {key: len(ids & known) for key, known in seen_sets.items()},
            "unique_previous_overlap": len(ids & previous_ids),
            "metrics": {label: _metrics(rows) for label, rows in zip(
                ("patch_validation_fit", "deployed_coach", "rank_median"), records)},
            "patch_minus_deployed_mae": paired_bootstrap(records[0], records[1]),
        }
    return {"format": "patch-reference-vs-deployed-1", "patch": ref["policy"]["patch"],
            "reference_recipe": validation["winner"], "train_match_ids": validation["selection_train_ids"],
            "professional_weight_training_matches": sum(g["cohort"] == "professional" for g in train),
            "ranked_weight_training_matches": sum(g["cohort"] == "ranked_7300" for g in train),
            "validation_cutoff": validation["cutoff"], "validation_fit_sha256": canonical_hash(fit),
            "deployed_bundle_sha256": canonical_hash(deployed), "source_data_sha256": ref["source_data_hash"],
            "evaluation": evaluation,
            "interpretation": "Descriptive paired comparison of frozen methods. Negative deltas favour the patch recipe. Previous datasets overlap each other. Intervals resample whole matches and are per target. The featured high-MMR sample and the chronological holdout come from a limited period."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path, default=REPO / "examples")
    parser.add_argument("--out", type=Path, default=REPO / "data/patch-reference-comparison.json")
    args = parser.parse_args()
    for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[var] = "1"
    result = compare(args.examples)
    save_coach_model(result, args.out)
    for name, row in result["evaluation"].items():
        print(name, row["matches"], {label: [round(metrics[k]["mae"], 2) for k in TARGETS]
                                    for label, metrics in row["metrics"].items()} if row["matches"] else {})


if __name__ == "__main__":
    main()
