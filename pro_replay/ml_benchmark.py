"""Reproduce model selection first, then open the external evaluation corpus."""

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import time

from .coach_features import game_features
from .coach_model import LEGACY_MODEL_PATH, TARGETS, _prediction_errors, save_coach_model
from .ml_experiments import canonical_hash, evaluate_candidates, fit_candidate, make_protocol, select_model


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def read(path):
    opener = gzip.open if Path(path).suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def _games(raw, ranked=False):
    games, excluded = [], Counter()
    for replay in raw:
        game, reason = game_features(replay, ranked=ranked)
        if reason:
            excluded[reason] += 1
        else:
            games.append(game)
    return sorted(games, key=lambda g: (g["start"], g["match_id"])), dict(excluded)


def run_selection(out, progress=None):
    """No holdout file is opened by this stage. One CPU thread per native library."""
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ[key] = "1"
    try:
        from threadpoolctl import threadpool_limits
        import sklearn
    except ImportError as exc:
        raise ValueError("Для обучения установи зависимости из requirements-ml.txt.") from exc
    out = Path(out)
    legacy = read(LEGACY_MODEL_PATH)
    raw = read(EXAMPLES / "coach-ml-training-snapshots.json.gz")["matches"]
    training_ids = set(legacy["coverage"]["train_match_ids"])
    development, excluded = _games([r for r in raw if r["match_id"] in training_ids])
    if {g["match_id"] for g in development} != training_ids or excluded:
        raise ValueError("Frozen development corpus is incomplete.")
    protocol = make_protocol(development)
    # Written before fitting any candidate; the test corpus is opened only by evaluate.
    save_coach_model(protocol, out / "economy-protocol.json")
    started = time.monotonic()
    with threadpool_limits(limits=1):
        selection = select_model(development, protocol, progress=progress)
        save_coach_model(selection, out / "economy-selection.json")
        for recipe in protocol["recipes"]:
            if progress:
                progress({"stage": "final_fit", "candidate": recipe["name"]})
            predictor = fit_candidate(recipe, development, protocol["feature_names"])
            if recipe["name"] == selection["winner"]:
                predictor["uncertainty"] = selection["uncertainty"]
            save_coach_model(predictor, out / "candidates" / (recipe["name"] + ".json"))
    receipt = {"selected_recipe": selection["winner"], "protocol_sha256": protocol["sha256"],
               "selection_sha256": canonical_hash(selection),
               "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
               "wall_seconds": time.monotonic() - started, "python": platform.python_version(),
               "scikit_learn": sklearn.__version__, "cpu_threads": 1,
               "release_gate": "Winner fixed by development CV; promote only if every target MAE is lower than legacy ridge on fresh ranked games, with at least 50 eligible games.",
               "candidate_sha256": {r["name"]: canonical_hash(read(out / "candidates" / (r["name"] + ".json")))
                                    for r in protocol["recipes"]}}
    save_coach_model(receipt, out / "economy-selection-receipt.json")
    return receipt


def run_evaluation(out):
    """Report all preregistered candidates without retuning or changing the winner."""
    out = Path(out)
    selection = read(out / "economy-selection.json")
    receipt = read(out / "economy-selection-receipt.json")
    if canonical_hash(selection) != receipt["selection_sha256"]:
        raise ValueError("Selection changed after the models were frozen.")
    bundles = {name: read(out / "candidates" / (name + ".json")) for name in receipt["candidate_sha256"]}
    if any(canonical_hash(bundles[n]) != h for n, h in receipt["candidate_sha256"].items()):
        raise ValueError("A frozen candidate changed before evaluation.")
    legacy = read(LEGACY_MODEL_PATH)
    raw_pro = read(EXAMPLES / "coach-ml-training-snapshots.json.gz")["matches"]
    historical_ids = set(legacy["coverage"]["test_match_ids"])
    old_pro, pro_legacy_excluded = _games([r for r in raw_pro if r["match_id"] in historical_ids])
    if pro_legacy_excluded or {g["match_id"] for g in old_pro} != historical_ids:
        raise ValueError("Historical professional evaluation corpus is incomplete.")
    raw_ranked = read(EXAMPLES / "coach-ml-legacy-ranked-snapshots.json.gz")["matches"]
    old_ranked, old_excluded = _games(raw_ranked, True)
    manifest = read(EXAMPLES / "coach-ml-holdout-manifest.json")
    holdout_path = EXAMPLES / "coach-ml-holdout-snapshots.json.gz"
    if hashlib.sha256(holdout_path.read_bytes()).hexdigest() != manifest["snapshot_sha256"]:
        raise ValueError("Fresh evaluation corpus differs from its collection manifest.")
    raw_new = read(holdout_path)
    pro, pro_excluded = _games(raw_new["professional"])
    ranked, ranked_excluded = _games(raw_new["ranked"], True)
    known = set(legacy["coverage"]["train_match_ids"]) | historical_ids | {g["match_id"] for g in raw_ranked}
    fresh_ids = [g["match_id"] for g in pro + ranked]
    if known.intersection(fresh_ids) or len(fresh_ids) != len(set(fresh_ids)):
        raise ValueError("Fresh evaluation must be disjoint from all historical data.")
    if pro_excluded or ranked_excluded or any(g["start"] <= manifest["sampling_policy"]["strict_start_after"] for g in pro + ranked):
        raise ValueError("Fresh corpus no longer satisfies its registered eligibility and time boundary.")
    sets = {"legacy_professional": old_pro, "legacy_ranked": old_ranked,
            "fresh_professional": pro, "fresh_ranked": ranked}
    evaluation = evaluate_candidates(bundles, sets)
    name = selection["winner"]
    fresh = evaluation["fresh_ranked"]
    candidate = fresh["candidates"][name]["metrics"]
    reference = fresh["candidates"]["ridge_legacy"]["metrics"]
    passed = fresh["games"] >= 50 and all(candidate[k]["mae"] < reference[k]["mae"] for k in TARGETS)
    report = {"format": "economy-benchmark-1", "selection": selection, "receipt": receipt,
              "evaluation": evaluation, "release_gate": {"passed": passed, "winner": name,
                  "criterion": receipt["release_gate"], "selection_used_external_scores": False},
              "excluded": {"legacy_ranked": old_excluded, "fresh_professional": pro_excluded, "fresh_ranked": ranked_excluded},
              "limitations": ["Fresh ranked games are a narrow time-window convenience sample from one patch.",
                              "Only five eligible future professional games were available; those scores are exploratory.",
                              "Legacy benchmarks were already inspected previously and are descriptive.",
                              "Strict fresh no-disconnect selection differs from the older corpus.",
                              "No causal impact, best action or improvement in player MMR was measured."]}
    save_coach_model(report, out / "economy-comparison.json")
    fit = bundles[name]
    metrics, positions = _prediction_errors(fit, old_pro)
    ranked_metrics, _ = _prediction_errors(fit, ranked)
    bundle = {"format": "pro-replay-coach-2", "algorithm": name,
              "input_minute": 10, "target_minute": 20, "targets": list(TARGETS), "fit": fit,
              "metrics": metrics, "metrics_by_position": positions, "ranked_metrics": ranked_metrics,
              "coverage": deepcopy(legacy["coverage"]), "uncertainty": selection["uncertainty"],
              "selection_protocol_sha256": receipt["protocol_sha256"], "release_gate_passed": passed,
              "fresh_test_match_ids": fresh_ids,
              "limitations": report["limitations"]}
    save_coach_model(bundle, out / "pro-coach-model-v2.json")
    from .ml_report import render_benchmark
    (out / "economy-comparison.html").write_text(render_benchmark(report), encoding="utf-8")
    return report


def run_ranked_selection(out):
    """Select for the client domain; all previously scored pubs become development."""
    from .ranked_selection import select_ranked
    out = Path(out)
    first = read(out / "economy-selection-receipt.json")
    bundles = {name: read(out / "candidates" / (name + ".json")) for name in first["candidate_sha256"]}
    if any(canonical_hash(bundles[n]) != digest for n, digest in first["candidate_sha256"].items()):
        raise ValueError("Phase-one predictor changed.")
    old = read(EXAMPLES / "coach-ml-legacy-ranked-snapshots.json.gz")["matches"]
    prior = read(EXAMPLES / "coach-ml-holdout-snapshots.json.gz")["ranked"]
    games, _ = _games(old + prior, ranked=True)
    result = select_ranked(bundles, games, test_policy="retrospective")
    save_coach_model(result, out / "ranked-domain-selection.json")
    receipt = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(), "selection_sha256": canonical_hash(result),
               "winner": result["winner"], "release_gate": "At least 50 unseen ranked matches and lower MAE than legacy ridge on all three targets; no recipe switching after evaluation.",
               "test_kind": "independent_retrospective", "future_evaluation": "pending: no completed matches after the registered boundary were yet available"}
    save_coach_model(receipt, out / "ranked-selection-receipt.json")
    return receipt


def run_ranked_evaluation(out):
    out = Path(out)
    selected = read(out / "ranked-domain-selection.json")
    receipt = read(out / "ranked-selection-receipt.json")
    if canonical_hash(selected) != receipt["selection_sha256"]:
        raise ValueError("Ranked selection changed after freezing.")
    name = selected["winner"]
    bundles = {n: read(out / "candidates" / (n + ".json")) for n in {name, "ridge_legacy", "rank_median"}}
    if any(canonical_hash(bundle) != selected["candidate_sha256"][n] for n, bundle in bundles.items()):
        raise ValueError("A frozen candidate changed.")
    manifest = read(EXAMPLES / "coach-ml-ranked-retrospective-manifest.json")
    path = EXAMPLES / "coach-ml-ranked-retrospective-snapshots.json.gz"
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["snapshot_sha256"]:
        raise ValueError("Ranked test corpus changed from the collection manifest.")
    raw = read(path)
    games, excluded = _games(raw["ranked"], ranked=True)
    policy = manifest["sampling_policy"]
    if {g["match_id"] for g in games} != set(manifest["match_ids"]) or len(games) != manifest["matches"]:
        raise ValueError("Ranked test IDs differ from the registered manifest.")
    if any(not policy["window_start_inclusive"] <= g["start"] < policy["window_end_exclusive"] for g in games):
        raise ValueError("Ranked test lies outside the registered retrospective window.")
    if any(r.get("human_players") != 10 or any(p.get("leaver_status") != 0 for p in r["players"]) for r in raw["ranked"]):
        raise ValueError("Ranked test violates the no-disconnect ten-human eligibility policy.")
    seen = set(selected["development_match_ids"])
    seen.update(policy["excluded_prior_match_ids"])
    # Also reject historical excluded/short matches and all previous professional matches.
    seen.update(r["match_id"] for r in read(EXAMPLES / "coach-ml-legacy-ranked-snapshots.json.gz")["matches"])
    seen.update(r["match_id"] for r in read(EXAMPLES / "coach-ml-training-snapshots.json.gz")["matches"])
    seen.update(r["match_id"] for r in read(EXAMPLES / "coach-ml-holdout-snapshots.json.gz")["professional"])
    seen.update(r["match_id"] for r in read(EXAMPLES / "coach-ml-holdout-snapshots.json.gz")["ranked"])
    if seen & {g["match_id"] for g in games} or excluded:
        raise ValueError("Ranked test contains previously used or ineligible matches.")
    bundles[name]["uncertainty"] = selected["uncertainty"]
    result = evaluate_candidates(bundles, {"independent_retrospective_ranked": games})
    test = result["independent_retrospective_ranked"]
    new = test["candidates"][name]["metrics"]
    old = test["candidates"]["ridge_legacy"]["metrics"]
    passed = len(games) >= 50 and all(new[k]["mae"] < old[k]["mae"] for k in TARGETS)
    report = {"format": "ranked-client-benchmark-1", "selection": selected, "receipt": receipt,
              "evaluation": result, "release_gate": {"passed": passed, "winner": name, "criterion": receipt["release_gate"]},
              "test_manifest_sha256": canonical_hash(manifest),
              "limitations": ["This final check uses unseen historical matches; it is not forward-time validation after ranked development.",
                              "The strict future cohort remains empty and its registered policy is retained.",
                              "Only one patch, complete parsed games lasting twenty minutes with no recorded disconnects.",
                              "Recorded economy, not causal impact or optimal fight decisions, is the prediction target."]}
    save_coach_model(report, out / "ranked-model-comparison.json")
    legacy = read(LEGACY_MODEL_PATH)
    ranked_metrics, _ = _prediction_errors(bundles[name], games)
    model = {**legacy, "format": "pro-replay-coach-2", "algorithm": "domain_specific_economy",
             "ranked_fit": bundles[name], "ranked_metrics": ranked_metrics,
             "uncertainty": selected["uncertainty"], "release_gate_passed": passed,
             "ranked_development_match_ids": selected["development_match_ids"],
             "fresh_test_match_ids": [g["match_id"] for g in games],
             "selection_protocol_sha256": selected["sha256"], "test_kind": "independent_retrospective",
             "limitations": report["limitations"]}
    save_coach_model(model, out / "ranked-client-model.json")
    from .ml_report import render_benchmark
    (out / "ranked-comparison.html").write_text(render_benchmark(report), encoding="utf-8")
    return report
