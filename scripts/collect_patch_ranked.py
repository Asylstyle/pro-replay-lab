"""Capture OpenDota lobby MMR, then join completed matches by ID and roster.

Each invocation captures one /live response. Repeated runs retain earlier
observations so games can finish and become parsed. The SQLite cache stores
only the lobby evidence and compact economy samples, without account IDs.
"""

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from pro_replay.reference_policy import (LIVE_SOURCE, MINIMUM_AVG_MMR,
                                         compact_live_evidence, make_policy, reference_eligibility)
from pro_replay.source import OpenDota, SourceError, valve_patches
from scripts.rebuild_ml_snapshots import compact


def capture(db, rows, observed):
    counts = Counter()
    with db:
        for row in rows:
            try:
                evidence = compact_live_evidence(row, observed_at_utc=observed)
            except ValueError:
                counts["missing_mmr_or_roster"] += 1
                continue
            if (evidence["lobby_type"], evidence["game_mode"], evidence["league_id"]) != (7, 22, 0):
                counts["outside_ranked_all_pick"] += 1
                continue
            mid = evidence["match_id"]
            rating = evidence["average_mmr"]
            if rating < MINIMUM_AVG_MMR:
                counts["below_7300"] += 1
                status = "below_7300"
            else:
                counts["qualifying_observations"] += 1
                status = "awaiting_postgame"
            # A contradiction at either side of the cutoff invalidates a match,
            # regardless of which observation arrived first.
            db.execute("""INSERT INTO candidates(mid, rating, evidence, updated, status)
                          VALUES(?,?,?,?,?) ON CONFLICT(mid) DO UPDATE SET
                          status=CASE WHEN candidates.rating != excluded.rating THEN 'conflicting_mmr' ELSE candidates.status END,
                          card=CASE WHEN candidates.rating != excluded.rating THEN NULL ELSE candidates.card END""",
                       (mid, rating, json.dumps(evidence), evidence["last_update_time"], status))
    return dict(counts)


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS candidates (
        mid INTEGER PRIMARY KEY, rating INTEGER NOT NULL, evidence TEXT NOT NULL,
        updated INTEGER NOT NULL, status TEXT NOT NULL, card TEXT, attempt INTEGER DEFAULT 0)""")


def complete(db, api, policy, limit):
    pending = db.execute("""SELECT mid,evidence FROM candidates
        WHERE updated>=? AND card IS NULL AND status IN
        ('awaiting_postgame','incomplete_roster_or_snapshots','incomplete_ten_human_roster',
         'match_unfinished_at_cutoff','missing_match_metadata','network_failure')
        ORDER BY attempt, mid LIMIT ?""", (policy["patch_start"], limit)).fetchall()
    for mid, evidence_text in pending:
        result = None
        try:
            raw = api.match(mid)
            evidence = json.loads(evidence_text)
            cohort, status = reference_eligibility(raw, policy, public_match=evidence)
            if cohort == "ranked_7300":
                result = compact(raw)
                result["opendota_live"] = evidence
                status = "included"
            elif status is None:
                status = "postgame_mode_changed"
        except SourceError as exc:
            if exc.status in (401, 403, 429):
                raise
            status = "awaiting_postgame" if exc.status == 404 else "network_failure"
        with db:
            db.execute("""UPDATE candidates SET status=?,card=?,attempt=?
                          WHERE mid=? AND status!='conflicting_mmr'""",
                       (status, json.dumps(result, separators=(",", ":")) if result else None,
                        int(time.time()), mid))


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
        name = tmp.name
        tmp.write(data)
    try:
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def export(db, policy, out, capture_counts, observed):
    matches, ledger = [], []
    for mid, status, evidence, card in db.execute(
            """SELECT mid,status,evidence,card FROM candidates WHERE updated>=?
               AND status!='below_7300' ORDER BY mid""", (policy["patch_start"],)):
        if card:
            raw = json.loads(card)
            cohort, reason = reference_eligibility(raw, policy)
            if cohort == "ranked_7300":
                matches.append(raw)
            else:
                status = reason
        ledger.append({"match_id": mid, "status": status, "evidence": json.loads(evidence)})
    matches.sort(key=lambda r: (r["start_time"], r["match_id"]))
    data = gzip.compress(json.dumps({"matches": matches}, separators=(",", ":")).encode(), mtime=0)
    result = {"source": LIVE_SOURCE, "postgame_source": "https://api.opendota.com/api/matches/{match_id}",
              "capture_utc": observed, "capture_counts": capture_counts, "policy": policy,
              "included": len(matches), "matches": [r["match_id"] for r in matches],
              "candidate_status_counts": dict(Counter(row["status"] for row in ledger)),
              "included_with_nonzero_or_unknown_leaver_status": sum(
                  any(p.get("leaver_status") != 0 for p in raw["players"]) for raw in matches),
              "snapshot_sha256": hashlib.sha256(data).hexdigest(), "observations": ledger,
              "scope": "Featured live games captured by OpenDota; coverage depends on observation times and completed parsed cards. average_mmr is the reported numeric lobby average; individual MMRs and its denominator are not published."}
    _atomic(out / "patch-reference-ranked.json.gz", data)
    _atomic(out / "patch-reference-ranked-manifest.json", (json.dumps(result, indent=2) + "\n").encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=REPO / "data/patch-live.sqlite")
    parser.add_argument("--out", type=Path, default=REPO / "data/patch-references")
    parser.add_argument("--limit", type=int, default=100, help="Maximum pending postgame lookups per run; 0 captures only.")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be nonnegative")
    calendar = valve_patches()
    api = OpenDota()
    rows = api._get("/live")
    if not isinstance(rows, list):
        raise SourceError("OpenDota /live returned an invalid response")
    observed = datetime.now(timezone.utc).isoformat()
    args.cache.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(args.cache, timeout=30)) as db:
        initialize(db)
        counts = capture(db, rows, observed)
        policy = make_policy(calendar, as_of=int(time.time()))
        complete(db, api, policy, args.limit)
        result = export(db, policy, args.out, counts, observed)
    print(json.dumps({key: result[key] for key in ("policy", "included", "candidate_status_counts")}, indent=2))


if __name__ == "__main__":
    main()
