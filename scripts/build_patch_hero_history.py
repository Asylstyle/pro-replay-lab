"""Rebuild match-wide hero metrics from full OpenDota cards for the active reference cohorts."""

import argparse
import gzip
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pro_replay.hero_review import extract_hero_samples
from pro_replay.patch_reference import client_cohort, load_reference
from scripts.collect_patch_pro import atomic_write


def _compact(path, model, cohort, mid):
    with gzip.open(path, "rt", encoding="utf8") as source:
        raw = json.load(source)
    policy = model["policy"]
    start, duration = raw.get("start_time"), raw.get("duration")
    if (raw.get("match_id") != mid or client_cohort(raw, model) != cohort
            or type(start) is not int or type(duration) is not int or duration <= 0
            or start < policy["patch_start"] or start + duration > policy["as_of"]):
        raise ValueError(f"Card identity, cohort or time conflicts with the verified match {mid}")
    samples = extract_hero_samples(raw)
    coverage = (len(samples), sum(s["position"] is not None for s in samples),
                sum(v is not None for s in samples for v in s["values"].values()), raw.get("version") or 0)
    row = {k: raw[k] for k in ("match_id", "start_time", "duration", "patch", "lobby_type", "game_mode")}
    return coverage, {**row, "league_id": raw["leagueid"], "patch_version": policy["patch"], "hero_samples": samples}


def build(model, directories):
    files = {}
    for directory in directories:
        for path in directory.glob("*.json.gz"):
            stem = path.name.removesuffix(".json.gz").removeprefix("raw-")
            if stem.isascii() and stem.isdecimal():
                files.setdefault(int(stem), set()).add(path)
    rows = []
    for cohort, match_ids in model["cohort_ids"].items():
        for mid in match_ids:
            if mid not in files:
                raise ValueError(f"Full OpenDota card missing for match {mid}")
            # OpenDota may supply the scoreboard before replay parsing fills in
            # roles, stuns and wards. Choose one complete card across all caches.
            cards = (_compact(path, model, cohort, mid) for path in sorted(files[mid]))
            coverage, row = max(cards, key=lambda pair: pair[0])
            if coverage[0] != 10 or not any(s["values"]["damage"] is not None for s in row["hero_samples"]):
                raise ValueError(f"Full player metrics missing for match {mid}")
            rows.append(row)
    return sorted(rows, key=lambda row: row["match_id"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, help="Verified patch reference model; defaults to the active model.")
    parser.add_argument("--raw-dir", type=Path, nargs="+", required=True, help="Folders with full ID.json.gz or raw-ID.json.gz cards.")
    parser.add_argument("--output", type=Path, default=REPO / "data" / "patch-hero-reference.json.gz")
    args = parser.parse_args()
    rows = build(load_reference(args.model), args.raw_dir)
    encoded = json.dumps(rows, separators=(",", ":"), allow_nan=False).encode()
    atomic_write(args.output, gzip.compress(encoded, mtime=0))
    print(f"{len(rows)} matches written to {args.output}")
