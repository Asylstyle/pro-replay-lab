"""Rebuild the verified pub comparison from a ranked snapshot and full OpenDota logs."""

import argparse
import gzip
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from pro_replay.fight_coach import CANDIDATES
from pro_replay.pro_purchases import RANKED_REFERENCE, fit_ranked_purchase_reference, write_reference
from scripts.evaluate_fresh_ranked import read_capture


def read_games(ids, folders):
    for mid in ids:
        names = (f"{mid}.json.gz", f"raw-{mid}.json.gz", f"{mid}.json")
        paths = [p / name for p in folders for name in names if (p / name).is_file()]
        if not paths:
            raise ValueError(f"Missing full OpenDota log for {mid}")
        for path in paths:
            payload = path.read_bytes()
            raw = json.loads(gzip.decompress(payload) if path.suffix == ".gz" else payload)
            if raw.get("match_id") != mid:
                raise ValueError(f"Wrong match ID in {path}")
            yield raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, default=REPO / "examples/fresh-ranked-2026-11-17")
    parser.add_argument("--raw", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, default=RANKED_REFERENCE)
    args = parser.parse_args()
    cards, manifest = read_capture(args.capture / "matches.json.gz", args.capture / "manifest.json")
    observations = {card["match_id"]: card["opendota_live"] for card in cards}
    tracked = {key for keys in CANDIDATES.values() for key in keys}
    data = fit_ranked_purchase_reference(read_games(observations, args.raw), observations, manifest["policy"], tracked)
    write_reference(data, args.output)
    print(f'{len(data["games"])} verified pub matches, {sum(len(g["fights"]) for g in data["games"])} fight intervals')


if __name__ == "__main__":
    main()
