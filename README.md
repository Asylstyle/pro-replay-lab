# Pro Replay Lab

A local Dota 2 replay coach built with **Python, Java and SQLite**. Enter a match ID and pick a hero to review up to three decisions. For each replay-verified suggestion, the report names the game second, target and action and shows which conditions were met. A separate CPU-trained model estimates the player's economy at minute 20 from the first ten minutes.

[Руководство на русском](README.ru.md) · [Replay decoder](replay-decoder/README.md) · [Model comparison](docs/RANKED_93_COMPARISON.md)

## What a replay can tell you

At **04:07** in match **9093297478**, Rubick could cast Telekinesis on Venomancer. The replay puts the target **368 units** away, within the **550-unit** cast range. The spell was ready, Rubick had **302 mana** for a **110-mana** cast, and Radiant had vision of the target. The report gives the player a specific moment to revisit. Whether the cast would have won the fight remains an open coaching question. [Rubick's saved report](examples/coach-decisions.html) · [Match on OpenDota](https://www.opendota.com/matches/9093297478)

An earlier journal-based suggestion asked Pudge to interrupt Black Hole. Decoding the replay placed him **over 5,000 units** from the target, so the coach discarded the suggestion. The same replay contained a feasible Dismember at **08:48**, from **96 units** away. [Replay evidence and sources](examples/SOURCES.md)

<details>
<summary>See a player report from a professional match</summary>

![Yatoro's Ursa match report, showing teamfight participation, building damage and comparisons with earlier carry performances](examples/yatoro-ursa.png)

Yatoro bought Blink Dagger at **16:30** and killed Razor nine seconds later. He dealt **17,716 of his team's 21,749 building damage**. The report puts his numbers alongside earlier games on the same hero and role. To explore the tables, open the HTML file from a downloaded copy of the repository. [Full offline report](examples/yatoro-ursa.html)

</details>

## How the system works

| Stage | Engineering work |
| --- | --- |
| Match collection | Python joins OpenDota postgame records to Valve's patch calendar. For ranked data, the collector captures each lobby's average MMR while the game is live and checks its heroes and teams against the final record. Saved snapshots and manifests let an experiment run again offline. [Eligibility rules](pro_replay/reference_policy.py) |
| Replay decoding | A Java 21 reader built on Clarity parses Valve Source 2 replay files. It checks the embedded match ID, samples ten heroes once per game second and extracts casts and deaths with fractional timestamps. Python downloads the pinned decoder dependencies, verifies their SHA-256 hashes and indexes compressed frames in SQLite. Multiple hero reviews reuse the same decoded game. [Decoder and schema](replay-decoder/README.md) · [Frame index](pro_replay/replay_files.py) |
| Decision checks | Python tests whether the actor is alive and able to act, the ability or active inventory item is ready, mana is sufficient, the team sees the target and it is in range. Interrupts also require an active channel. The report distinguishes recorded actions, feasible alternatives and hypotheses based on the match journal. [Verification logic](pro_replay/replay_verification.py) |
| Storage and delivery | A local browser interface and a CLI share the coaching pipeline. SQLite stores the player archive, match reviews and role references. Reports export as standalone HTML. When a replay has expired, the available journal evidence can still support a partial review. [Browser app](pro_replay/coach_app.py) · [Archive](pro_replay/archive.py) |
| Model training and inference | Training and inference share the economy feature builder. The model uses hero, early last hits, gold, XP and team context. Training runs on CPU with NumPy and scikit-learn. The coach reads the fitted JSON weights using the Python standard library. [Features](pro_replay/coach_features.py) · [Training and selection](pro_replay/patch_reference.py) |

## What the coach analyses

- **Fights and builds.** Connects item purchase times to incoming threats, item activations, damage, control, deaths and subsequent objectives. Player contribution is a weighted share of six team measures. Its weights are explicit, and the index describes contribution within one team. The project has no measured causal estimate of how a decision changes win probability. [Fight and build review](pro_replay/build_review.py) · [Contribution calculation](pro_replay/impact.py)
- **Mid matchups.** During the first ten minutes, compares a trade with securing the ranged creep. It checks spell rank and damage, the opponent's available response and a rotating support's distance and disable. In match 9088199723, Rubick's Fade Bolt could deal **165 damage** to a **155-HP** ranged creep at **03:12**. [Mid-lane calculations and coverage](docs/MID_MATCHUPS.md) · [Rubick's lane report](examples/coach-mid-nukes.html)
- **Support and map decisions.** Tracks lane and neutral creep movement to flag potential pulls and wave drags. Smoke reviews bring together nearby allies, item readiness, visible enemies and the next objective. The report identifies the evidence and leaves route choice to the reviewer. [Creep tracking](docs/SUPPORT_WAVES.md) · [Smokes and objectives](docs/ACCEPT_AND_MACRO.md)
- **Learning across five positions.** A local TF-IDF index retrieves timestamped lessons by role, hero, item and topic. It contains **73 fragments from 17 sources**, including **31 caption-checked fragments from seven videos**. Match timestamps and video timestamps are shown separately. [Lesson sources](examples/coach-lessons-sources.json) · [Retrieval implementation](pro_replay/coach_lessons.py)

## Forecasting economy at 20:00

Ranked All Pick and professional games have separate reference populations. On patch **7.41f**, the professional reference uses **307 matches**. For ranked play, the published default uses **53 matches**, and the candidate was refitted on **all 93 qualifying matches** (930 player performances). Five recipes competed across three expanding time splits, including a median baseline and Ridge regressions. All ten players stay in the same split, and games still running at the test boundary are excluded from that split's training data. The 93-game candidate selected Ridge with richer inputs and regularization strength 10. [Cohort design](docs/PATCH_REFERENCE.md) · [Frozen candidate](examples/ranked-93-candidate/model.json)

Both versions predicted the same **80 players in eight unseen games**, with lobby averages of **7,389–8,164 MMR**. The table reports mean absolute error per player. Lower values indicate closer predictions.

| Target at 20:00 | 53-game model | 93-game model | Error reduction | Games with lower error |
| --- | ---: | ---: | ---: | ---: |
| Cumulative gold | 743.4 | **710.3** | 4.45% | 5 of 8 |
| Cumulative XP | 1,169.3 | **1,129.7** | 3.38% | 4 of 8 |

All eight games started after the final training match ended and before the weights were frozen. Their final records arrived later. Paired 95% intervals from resampling whole matches include zero for both targets, so the 53-game version remains the default. [Per-match results and reproduction](docs/RANKED_93_COMPARISON.md) · [Machine-readable evaluation](examples/ranked-93-candidate/late/evaluation.json)

Two earlier experiments record where added complexity failed. In a **12-recipe** comparison, the predictor selected on professional games increased all three errors on the first 100 unseen ranked games. In a separate item-purchase task on 100 Yatoro games, softmax regression and the per-hero frequency baseline both predicted **12 of 20** items correctly. [Economy error analysis](docs/ML_ERROR_ANALYSIS.md) · [Item classifier and baselines](docs/item-model-card.md)

## Try it

**Offline demo.** From the repository root, use Python 3.10+ to create a report from the bundled matches and find towers Yatoro finished before buying Manta Style. On Windows, `py` also works in place of `python`.

```sh
python -m pro_replay demo
python -m pro_replay find demo tower item:manta
```

Open the generated `yatoro.html` in your browser. The event query returns the hero, both timestamps and a match link. On a synced player archive you can also search for a sequence within a time limit, such as a kill within 120 seconds of buying Blink Dagger. The output counts incomplete logs separately. [Query examples](README.ru.md#свои-запросы-к-архиву)

**Review a match.** Install Java 21+ for full replay decoding. Start the local app, enter the match ID and select a hero. The first live review needs internet access to fetch the match and decoder dependencies. On Python 3.10–3.13, install `backports.zstd` for Zstandard-compressed replays. Windows users can also open [start-coach.cmd](start-coach.cmd).

```sh
python -m pro_replay school
```

**Reproduce the ML result.** The first command scores the two published models using the bundled data, without downloading matches. To retrain the candidate from scratch, run the last two commands in a Python 3.14 virtual environment.

```sh
python scripts/compare_ranked_93.py --capture examples/ranked-93-candidate/late --late-roster examples/ranked-93-candidate/late/roster.json --out data/ranked-93-evaluation.json
python -m pip install -r requirements-ml.txt
python -m pro_replay train-reference --frozen-policy examples/ranked-collection-2026-11-18/manifest.json --out data/rebuilt-93.json
```

## Verification

The 287-test regression suite covers cohort eligibility, leakage at time boundaries, replay identity and state checks, missing data, the contribution index and HTML report generation. GitHub Actions runs it on Ubuntu with the pinned ML dependencies. Two end-to-end checks decoded **5,708 one-second frames** from real replays. A separate nine-replay audit examined mid-lane exchanges. [Tests](tests/) · [CI workflow](.github/workflows/tests.yml) · [Replay sources](examples/SOURCES.md) · [Mid-lane audit](examples/mid-exchange-check.json)

Replay sampling gives team vision at each recorded second. A player's camera, attention and the outcome of an unplayed alternative remain unknown. The published ability and item calculations target **7.41f**.

```sh
python -m unittest discover -s tests -v
```
