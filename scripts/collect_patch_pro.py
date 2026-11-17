"""Collect compact economy snapshots of verified pro matches on the current Valve patch."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rebuild_ml_snapshots import compact
from pro_replay.reference_policy import make_policy, reference_eligibility
from pro_replay.source import OpenDota, SourceError, VALVE_PATCHES, valve_patches


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        stage = Path(stream.name)
        try:
            stream.write(data)
        except BaseException:
            stream.close()
            stage.unlink(missing_ok=True)
            raise
    try:
        stage.replace(path)
    finally:
        stage.unlink(missing_ok=True)


def encoded_corpus(matches):
    payload = {"schema": "pro-replay-ml-snapshots-v1", "matches": matches}
    return gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), compresslevel=9, mtime=0)


def cache_index(folders):
    files = {}
    for folder in folders:
        if folder.exists() and not folder.is_dir():
            raise ValueError(f"Cache path is not a directory: {folder}")
        for path in sorted(folder.glob("*.json.gz")):
            stem = path.name.removesuffix(".json.gz")
            if stem.isascii() and stem.isdecimal():
                files.setdefault(int(stem), path)
    return files


def load_game(mid, files, api, download_dir):
    path = files.get(mid, download_dir / f"{mid}.json.gz")
    if path.exists():
        with gzip.open(path, "rt", encoding="utf8") as stream:
            raw = json.load(stream)
        if raw.get("match_id") != mid:
            raise ValueError(f"Cached game ID differs from filename: {mid}")
    else:
        raw = api.match(mid)
        path = download_dir / f"{mid}.json.gz"
        atomic_write(path, gzip.compress(json.dumps(raw, separators=(",", ":")).encode(), compresslevel=2, mtime=0))
        files[mid] = path
    return raw


def collect(folders, output, limit, download_dir, *, rebuild=None, allow_empty=False):
    api = OpenDota()
    files = cache_index(folders or [download_dir])
    if rebuild:
        manifest = json.loads(rebuild.read_text(encoding="utf8"))
        matches = []
        for mid in manifest["match_ids"]:
            raw = load_game(mid, files, api, download_dir)
            cohort, why = reference_eligibility(raw, manifest["reference_policy"])
            if cohort != "professional":
                raise ValueError(f"Frozen match {mid} fails eligibility: {why}")
            matches.append(compact(raw))
        data = encoded_corpus(matches)
        if hashlib.sha256(data).hexdigest() != manifest["snapshot_sha256"]:
            raise ValueError("Frozen corpus differs from its recorded hash; check raw cache or upstream changes.")
        atomic_write(output / "patch-reference-pro.json.gz", data)
        atomic_write(output / "patch-reference-pro-manifest.json", rebuild.read_bytes())
        return manifest

    as_of = int(time.time())
    calendar = sorted(valve_patches())
    policy = make_policy(calendar, as_of=as_of)
    start, patch = policy["patch_start"], policy["patch"]
    matches, excluded, per_match, seed_ids, discovered_ids = {}, Counter(), {}, [], []
    snapshot_path = output / "patch-reference-pro.json.gz"
    manifest_path = output / "patch-reference-pro-manifest.json"
    retained_ids, preceding_sha = [], None
    prior_manifest, prior_snapshot = manifest_path, snapshot_path
    if not folders and not manifest_path.exists() and not snapshot_path.exists():
        prior_manifest = REPO / "examples" / manifest_path.name
        prior_snapshot = REPO / "examples" / snapshot_path.name
    if prior_manifest.exists() or prior_snapshot.exists():
        if not (prior_manifest.exists() and prior_snapshot.exists()):
            raise ValueError("Incomplete prior pro snapshot/manifest pair; restore the pair before refreshing.")
        previous = json.loads(prior_manifest.read_text(encoding="utf8"))
        if previous["patch"] == patch:
            prior_bytes = prior_snapshot.read_bytes()
            preceding_sha = hashlib.sha256(prior_bytes).hexdigest()
            if preceding_sha != previous["snapshot_sha256"]:
                raise ValueError("Prior pro snapshot differs from its collection manifest.")
            archived = json.loads(gzip.decompress(prior_bytes))["matches"]
            if [game["match_id"] for game in archived] != previous["match_ids"]:
                raise ValueError("Prior pro snapshot IDs differ from its collection manifest.")
            for raw in archived:
                cohort, reason = reference_eligibility(raw, policy)
                if cohort != "professional":
                    raise ValueError(f"Cannot carry previously accepted match {raw['match_id']}: {reason}")
                matches[raw["match_id"]] = raw
            retained_ids = sorted(matches)
    for mid in sorted(files):
        raw = load_game(mid, files, api, download_dir)
        # Public games share our cache but never enter this professional branch.
        if not raw.get("leagueid"):
            continue
        seed_ids.append(mid)
        cohort, reason = reference_eligibility(raw, policy)
        if cohort != "professional":
            if mid in matches:
                raise ValueError(f"New raw data contradicts the previous snapshot for match {mid}: {reason}")
            excluded[reason] += 1
            per_match[str(mid)] = reason
        else:
            matches[mid] = compact(raw)
    page_limit = min(500, max(100, 3 * limit))
    sql = ("SELECT m.match_id, m.start_time, m.duration, m.leagueid, l.tier "
           "FROM matches m JOIN leagues l ON l.leagueid=m.leagueid "
           f"WHERE m.start_time >= {start} AND m.start_time + m.duration <= {as_of} "
           "AND m.lobby_type=1 AND m.duration>=1200 AND l.tier IN ('premium','professional') "
           f"ORDER BY m.match_id DESC LIMIT {page_limit}")
    pages, failures = [], {}
    if limit:
        discovery = api._get("/explorer", {"sql": sql})
        if not isinstance(discovery, dict) or discovery.get("err") or not isinstance(discovery.get("rows"), list):
            raise ValueError("OpenDota /explorer did not return the verified league list.")
        pages = list(dict.fromkeys(int(row["match_id"]) for row in discovery["rows"]))
    accepted, consecutive_errors = 0, 0
    for mid in pages:
        if mid in files or mid in matches:
            continue
        try:
            raw = load_game(mid, files, api, download_dir)
            consecutive_errors = 0
        except SourceError as exc:
            failures[str(mid)] = str(exc)
            consecutive_errors += 1
            if consecutive_errors >= 3:
                break
            continue
        discovered_ids.append(mid)
        cohort, why = reference_eligibility(raw, policy)
        if cohort != "professional":
            excluded[why] += 1
            per_match[str(mid)] = why
            continue
        matches[mid] = compact(raw)
        accepted += 1
        if accepted % 10 == 0:
            print(f"Selected {accepted} additional eligible games.", flush=True)
        if accepted >= limit:
            break
    if not matches and (not allow_empty or failures):
        raise ValueError(f"No eligible professional matches on patch {patch}.")
    ordered = sorted(matches.values(), key=lambda game: (game["start_time"], game["match_id"]))
    data = encoded_corpus(ordered)
    manifest = {
        "schema": "pro-replay-patch-reference-pro-v1",
        "patch": patch, "patch_start_inclusive": start, "patch_end_exclusive": next(
            (t for t, _ in calendar if t > start), None),
        "as_of_epoch": as_of, "as_of_utc": datetime.fromtimestamp(as_of, timezone.utc).isoformat(),
        "patch_calendar_source": VALVE_PATCHES, "patch_notes": f"https://www.dota2.com/patches/{patch}",
        "patch_assignment": "Valve patch release timestamp joined to match start_time (seconds UTC).",
        "reference_policy": policy,
        "selection": "Shared reference_eligibility policy for verified professional/premium leagues with ten humans in ten slots, complete cumulative LH/gold/XP observations at 600 and 1200, matching league identity and a finished match on the active Valve patch.",
        "pro_source": "https://api.opendota.com/api/explorer", "match_source": "https://api.opendota.com/api/matches/{id}",
        "seed_selection": "All eligible tournament cards from supplied OpenDota seed caches. Existing ID overlap is expected. Then newest eligible games outside the seed caches from one bounded deterministic Data Explorer page. Download cache is reused.",
        "retained_current_patch_ids": retained_ids, "previous_snapshot_sha256": preceding_sha,
        "seed_ids_examined": seed_ids, "discovery_sql": sql, "discovery_ids": pages,
        "new_ids_examined": discovered_ids, "new_matches_accepted": accepted, "new_match_target": limit,
        "fetch_errors": failures, "excluded_counts": dict(sorted(excluded.items())), "excluded_ids": per_match,
        "match_ids": [g["match_id"] for g in ordered], "matches": len(ordered),
        "min_start": ordered[0]["start_time"] if ordered else None,
        "max_start": ordered[-1]["start_time"] if ordered else None,
        "league_count": len({g["leagueid"] for g in ordered}), "tier_counts": dict(Counter(g["league"]["tier"] for g in ordered)),
        "snapshot_sha256": hashlib.sha256(data).hexdigest(),
        "limitations": "Bounded convenience sample drawn from prior caches and one recent discovery page. No match result, prediction or error used in discovery. Professional matches qualify by league without a public MMR threshold.",
    }
    atomic_write(snapshot_path, data)
    atomic_write(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, nargs="+", default=[],
                        help="Optional local OpenDota caches used to seed collection")
    parser.add_argument("--download-dir", type=Path, default=Path("data/open-dota-raw"),
                        help="Read-through cache for ID lookups after discovery")
    parser.add_argument("--out", type=Path, default=Path("data/patch-references"))
    parser.add_argument("--limit", type=int, default=80, choices=range(0, 501), metavar="0..500",
                        help="Maximum additional eligible games beyond local caches")
    parser.add_argument("--rebuild", type=Path, help="Rebuild frozen IDs and verify checksum using this manifest")
    args = parser.parse_args()
    result = collect(args.raw_dir, args.out, args.limit, args.download_dir, rebuild=args.rebuild)
    print(f"{result['patch']} · {result['matches']} matches · {result['snapshot_sha256']}")
