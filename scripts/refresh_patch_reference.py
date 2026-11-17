"""Collect current OpenDota references and publish a trained trainer model."""

import argparse
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pro_replay.coach_model import save_coach_model
from pro_replay.patch_reference import PACKAGED_MODEL, train_reference
from pro_replay.reference_policy import make_policy
from pro_replay.source import SourceError, valve_patches
from scripts import collect_patch_pro, collect_patch_ranked
from scripts.evaluate_fresh_ranked import read_capture


def packaged_inputs(examples):
    pros, pro_manifest = _snapshot(examples, "pro")
    fresh = examples / "fresh-ranked-2026-11-17"
    pubs, pub_manifest = read_capture(fresh / "matches.json.gz", fresh / "manifest.json")
    policy = pub_manifest["policy"]
    previous = pro_manifest["reference_policy"]
    if any(previous[k] != policy[k] for k in ("patch", "patch_start")) or previous["as_of"] > policy["as_of"]:
        raise ValueError("Packaged source snapshots must cover the same patch and precede the training cutoff.")
    return pros + pubs, policy


def _snapshot(folder, cohort):
    prefix = f"patch-reference-{cohort}"
    manifest = json.loads((folder / f"{prefix}-manifest.json").read_text(encoding="utf8"))
    data = (folder / f"{prefix}.json.gz").read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest["snapshot_sha256"]:
        raise ValueError(f"Snapshot checksum differs from its manifest: {prefix}")
    cards = json.loads(gzip.decompress(data))["matches"]
    expected = manifest["match_ids" if cohort == "pro" else "matches"]
    if [c["match_id"] for c in cards] != expected or len(set(expected)) != len(expected):
        raise ValueError(f"Snapshot match IDs differ from its manifest: {prefix}")
    return cards, manifest


def _restore_ranked_cache(cache, folders):
    """Merge frozen evidence with the persistent live cache, newest source first."""
    with closing(sqlite3.connect(cache, timeout=30)) as db:
        collect_patch_ranked.initialize(db)
        for folder in folders:
            if (folder / "patch-reference-ranked-manifest.json").is_file():
                cards, manifest = _snapshot(folder, "ranked")
            elif (folder / "manifest.json").is_file():
                cards, manifest = read_capture(folder / "matches.json.gz", folder / "manifest.json")
            else:
                continue
            by_id = {c["match_id"]: c for c in cards}
            included = {r["match_id"] for r in manifest["observations"] if r["status"] == "included"}
            if included != set(by_id):
                raise ValueError("Frozen ranked roster ledger differs from the published card IDs.")
            with db:
                for observation in manifest["observations"]:
                    mid, status = observation["match_id"], observation["status"]
                    evidence = observation["evidence"]
                    card = by_id.get(mid)
                    if mid != evidence["match_id"] or (status == "included") != (card is not None):
                        raise ValueError(f"Inconsistent frozen OpenDota observation for {mid}.")
                    if card is not None and card.get("opendota_live") != evidence:
                        raise ValueError(f"Card and roster evidence differ for {mid}.")
                    # Existing cache rows take precedence. Contradictory MMR in either
                    # the saved snapshots or a newer capture always disqualifies the ID.
                    db.execute("""INSERT INTO candidates(mid,rating,evidence,updated,status,card)
                        VALUES(?,?,?,?,?,?) ON CONFLICT(mid) DO UPDATE SET
                        status='conflicting_mmr', card=NULL
                        WHERE candidates.rating!=excluded.rating OR excluded.status='conflicting_mmr'""",
                               (mid, evidence["average_mmr"], json.dumps(evidence), evidence["last_update_time"],
                                status, json.dumps(card, separators=(",", ":")) if card is not None else None))
                    if card is not None:
                        # A packaged final card can complete an older pending observation.
                        db.execute("""UPDATE candidates SET status='included', card=?, evidence=?, updated=?
                            WHERE mid=? AND card IS NULL AND status IN ('awaiting_postgame','incomplete_roster_or_snapshots',
                            'incomplete_ten_human_roster','match_unfinished_at_cutoff','missing_match_metadata','network_failure')""",
                                   (json.dumps(card, separators=(",", ":")), json.dumps(evidence), evidence["last_update_time"], mid))


def refresh(data_dir, pro_limit, ranked_limit):
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    output = data_dir / "patch-references"
    ranked_cache = data_dir / "patch-live.sqlite"
    # Release this cross-platform process lock automatically, including on crashes.
    with closing(sqlite3.connect(data_dir / "patch-refresh-lock.sqlite", timeout=1)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        examples = REPO / "examples"
        _restore_ranked_cache(ranked_cache, [output, examples / "fresh-ranked-2026-11-17", examples])
        collect_patch_pro.collect([], output, pro_limit, data_dir / "open-dota-raw", allow_empty=True)
        subprocess.run([sys.executable, str(REPO / "scripts/collect_patch_ranked.py"),
                        "--cache", str(ranked_cache), "--out", str(output), "--limit", str(ranked_limit)],
                       check=True)
        calendar = valve_patches()
        policy = make_policy(calendar, as_of=int(time.time()))
        matches = []
        for cohort, policy_key in (("pro", "reference_policy"), ("ranked", "policy")):
            cards, manifest = _snapshot(output, cohort)
            previous = manifest[policy_key]
            if ((previous["patch"], previous["patch_start"]) != (policy["patch"], policy["patch_start"])
                    or previous["as_of"] > policy["as_of"]):
                raise ValueError("Patch changed during collection. Run the refresh again for the current patch.")
            matches.extend(cards)
        model = train_reference(matches, policy)
        target = data_dir / "patch-reference.json"
        save_coach_model(model, target)
        return model_summary(model, target)


def model_summary(model, target):
    return {"model": str(target), "patch": model["policy"]["patch"], "counts": model["counts"],
            "winners": {name: row["validation"]["winner"] for name, row in model["cohorts"].items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=REPO / "data")
    parser.add_argument("--rebuild-packaged", action="store_true",
                        help="Reproduce the shipped cohort models offline from the verified snapshots.")
    parser.add_argument("--pro-limit", type=int, default=80, help="Maximum new eligible professional matches.")
    parser.add_argument("--ranked-limit", type=int, default=100, help="Maximum pending postgame lookups.")
    args = parser.parse_args()
    if not 0 <= args.pro_limit <= 500 or args.ranked_limit < 0:
        parser.error("--pro-limit must be 0..500 and --ranked-limit must be nonnegative")
    try:
        if args.rebuild_packaged:
            model = train_reference(*packaged_inputs(REPO / "examples"))
            target = REPO / "examples" / PACKAGED_MODEL
            save_coach_model(model, target)
            result = model_summary(model, target)
        else:
            result = refresh(args.data_dir, args.pro_limit, args.ranked_limit)
    except (SourceError, OSError, ValueError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Refresh failed: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
