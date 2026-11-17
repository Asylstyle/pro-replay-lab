# ML data provenance and limits

These datasets contain OpenDota match records reduced to the fields needed for
the 10:00-to-20:00 economy benchmark. They are not replay files or labels of
optimal fighting decisions.

| Dataset | Games | Use |
| --- | ---: | --- |
| `coach-ml-training-snapshots.json.gz` | 500 professional | Original corpus; the first 400 chronological games are development data. The other 100 were already inspected in earlier work. |
| `coach-ml-legacy-ranked-snapshots.json.gz` | 120 ranked | Previously inspected descriptive comparison. One game ends before 20:00, leaving the same 119 eligible games as the original report. |
| `coach-ml-holdout-snapshots.json.gz` | 5 professional + 100 ranked | Phase-1 evaluation. The ranked cohort was subsequently promoted to development after a transfer failure; its results are not independent evidence for the final ranked candidate. |
| `coach-ml-ranked-retrospective-snapshots.json.gz` | 100 ranked | Unseen match IDs from a preregistered historical window, collected after the phase-2 candidate-selection plan. Independent of the 725 earlier records by match ID; not a forward-time test. |

All snapshots preserve the ten-player roster, hero IDs, patch, economy series
from minutes 0–10 and the values at minute 20. The model's input extractor reads
only observations available by 10:00. Minute-20 values are targets. Duration,
team identifiers and `position_est` are eligibility or audit metadata, never
predictors. `source_avg_rank_tier` is a reported lobby average, not a player's
verified individual rank. Net-worth trajectories are optional and must not be
silently treated as zero when missing.

## Phase-1 frozen future selection

The exact policy was saved before collection at **2026-11-17 17:35:27 UTC** in
`coach-ml-holdout-selection.json`. Its SHA-256 is recorded in the manifest and
the compressed snapshot file. No predictions, forecast errors or winning team
were used to choose candidates.

`sampling_policy_sha256` hashes the original registration file bytes, rather
than canonical JSON. `.gitattributes` preserves those files' original CRLF
line endings across platforms; tests verify both their hashes and their
equality to the policies embedded in the collection manifests.

The strict boundary is **2026-11-16 19:37:12 UTC**, the latest ending time among
the original 500 professional games. Every new game starts after that boundary
and is absent from both the original 500 professional and 120 ranked IDs.

Professional candidates came from OpenDota `/proMatches`, descending by match
ID. Of the 15 games available after the boundary, 5 passed the frozen criteria:
verified professional/premium league, both team identifiers, all ten complete
10:00 and 20:00 snapshots, and every recorded `leaver_status` equal to zero.
The other games had incomplete snapshots (1), missing team identifiers (4) or
nonzero disconnect status (5). The accepted sample covers 2 leagues and 32
heroes. It is too small for broad claims about professional performance.

Ranked candidates came from seven descending `/parsedMatches` pages, intersected
with `public_matches` through `/explorer` for Ranked All Pick. Collection
stopped at the first 100 eligible games, after excluding 37 games with nonzero
or unknown disconnect status, 5 ending before minute 20 and 1 with incomplete
snapshots. Reported lobby-rank groups contain 31 games below 50, 26 in 50–69 and
43 at 70 or higher. The sample covers 124 heroes and includes all five reported
positions, with 200 player rows per position.

These public games started within approximately 52 minutes on November 17.
This is a convenience sample of parsed games, not a representative sample of
all dates, regions or skill levels. The strict disconnect filter also differs
from the original professional corpus: 174 of its 500 games have a transient
disconnect (`leaver_status=1`), and none has a recorded status of 2 or higher.
The future cohort requires zero for every player. Keep this selection
difference visible when interpreting results.

## Phase-2 retrospective confirmation

After observing that the professional-development winner transferred poorly to
ranked games, the original 119 eligible public matches and the phase-1 100
public matches became ranked-domain development data. This is an explicit new
selection stage. It would be incorrect to report either group as an untouched
test of the candidate selected in this second stage. The original phase-1
snapshots and registration files remain unchanged for auditability.

A second strictly future policy was registered separately in
`coach-ml-ranked-final-selection.json`, using **2026-11-17 17:26:01 UTC** as the
latest ending time across all 725 previously seen records. At collection time,
OpenDota had no qualifying completed future public games. The corresponding
manifest records the shortage and the snapshot file is empty; this is not a
successful prospective validation.

The separately registered retrospective policy instead fixes the interval
**2026-11-16 00:00–12:00 UTC**, excludes all 725 previously used IDs and selects
the first 100 eligible parsed Ranked All Pick matches in descending ID order.
Registration occurred at **2026-11-17 17:51:34 UTC**, before candidate queries.
The interval follows the original 400 professional training games, whose
latest ending time was **2026-10-11 17:48:52 UTC**. However, some ranked-domain
development matches occur later than this new interval. This evaluates unseen
match IDs from the past; **it does not provide a forward-time guarantee**.

Selection uses only the fixed time interval, unseen IDs, complete ten-player
snapshots, at least 20 minutes of game duration and zero recorded disconnect
statuses. Neither match results nor forecast errors influence selection. The
separate retrospective manifest records candidate pages, exclusions and hashes.
Collection reached 100 games from 127 attempted candidates, excluding 27 with
nonzero or unknown disconnect status. The accepted games started between
**11:49:31 and 11:59:52 UTC** on November 16: the descending-ID policy produces
a narrow convenience sample within the registered 12-hour window. Lobby-rank
groups contain 24 games below 50, 16 in 50–69, 59 at 70 or higher and 1 with
unknown rank. There are 121 distinct heroes and 200 rows for each reported
position. There is no overlap with any of the 725 previous match IDs.
All 1,000 retrospective player rows have distinct known account IDs; 15 of
those accounts also occur in earlier data. Thus this is a split by match, not a
claim that every player is new. Account IDs are excluded from model inputs and
published snapshots.

## Reproduction and integrity

The manifests contain source IDs, candidate pages, exclusions, policy and
snapshot hashes. Full responses remain in the local ignored cache; the
published compact snapshots omit account identifiers, chat and unrelated
post-game scorecard fields. Feature extraction from compact and full records
was compared for all 500 original professional and 105 new holdout games and
matched exactly. It also matched for all 100 retrospective confirmation games.

From the repository root, recreate the compact files from raw compressed
responses named `<match_id>.json.gz`:

```powershell
py scripts/rebuild_ml_snapshots.py --dataset training --raw-dir data/ml-raw
py scripts/rebuild_ml_snapshots.py --dataset legacy-ranked --raw-dir data/ml-raw
py scripts/rebuild_ml_snapshots.py --dataset holdout --raw-dir data/ml-raw
py scripts/rebuild_ml_snapshots.py --dataset retrospective-ranked --raw-dir data/ml-raw
```

Use `--fetch-missing` to fetch absent records with the project's rate-limited
OpenDota client. By default outputs go to ignored `data/`, preserving the
published benchmark. `--output` chooses a different destination. The script
uses the frozen IDs and never substitutes better-looking matches. Upstream
responses can change, so keep the original raw cache when exact reproduction
is required and compare the printed SHA-256 with the manifests.
