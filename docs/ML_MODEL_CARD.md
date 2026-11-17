# Economy model: professional training, ranked-client validation

The released ranked predictor reduces observed last-hit MAE from **18.72 to 17.83** on 100 previously unseen historical games. Gold and XP point estimates also improve, but their paired 95% intervals include zero. This is a measured, limited improvement; no claim of 90% accuracy, causal impact or optimal fight decisions is made.

[Readable comparison](../examples/ranked-comparison.html) · [Machine-readable results](../examples/ranked-model-comparison.json) · [Error analysis](ML_ERROR_ANALYSIS.md) · [Data provenance](ML_DATA.md)

## Task and deployment

Predict cumulative last hits, gold and XP at 20:00 from observations available at 10:00. All model weights are fitted on the original 400 professional games, 4,000 player rows. Inputs include hero, within-team gold rank, exact 5- and 10-minute economy, recent resource gains, denies and both teams' economy. Missing values are imputed from the fitting fold only. Outcome, account identity, future events and retrospective position estimates never enter predictors.

The application chooses `ridge_rich_100` for Ranked All Pick clients. Professional and other matches retain the original professional predictor. The default [JSON bundle](../examples/ranked-client-model.json) contains both models; inference needs only Python's standard library. NumPy/scikit-learn are optional training dependencies. No pickle is loaded.

The interface shows an error estimate from the appropriate population, hero support, missing-input warnings and descriptive uncertainty bands. Previously used development and test matches are explicitly labelled. Subsequent evaluation excludes those IDs instead of presenting them as new evidence.

## Two experiments, preserved separately

1. **Professional selection.** Twelve fixed recipes: rank-median and extrapolation baselines; original Ridge; Ridge penalties 1/10/100 with richer inputs; base-feature ablations; Random Forest; Extra Trees; Huber and absolute-loss gradient boosting. Three expanding chronological folds inside 400 pro games keep all ten players together and exclude unfinished matches across boundaries. Candidates are selected by the mean target MAE relative to the rank-median baseline, avoiding mixing gold units with last hits. Training-only preprocessing and standardized targets prevent gold/XP from dominating tree fitting.
2. **Transfer failure.** Pro validation selected `ridge_rich_1`, but it increased all three errors on the first 100 unseen ranked games. It failed the registered release gate and was not activated. [Full first experiment](../examples/economy-model-comparison.json) and [readable report](../examples/economy-comparison.html) retain the negative result. The 100 legacy pro and 119 legacy ranked games were already inspected and remain descriptive benchmarks. Only five future professional games met eligibility, too few for broad claims.
3. **Ranked selection.** The 119 legacy ranked games and the first 100 ranked test games were explicitly reclassified as **219 development games**. The same twelve already-fitted predictors were ranked on that client population; no weights or recipes were changed. `ridge_rich_100` won. Its predictor hashes and selection were frozen at **2026-11-17 17:52:15 UTC**, before final scoring. [Selection](../examples/ranked-domain-selection.json) · [Receipt](../examples/ranked-selection-receipt.json).
4. **Independent historical test.** A separate policy registered at 17:51:34 UTC selected 100 previously unused games from the fixed November 16 window, excluding all 725 previous match IDs. They are later than professional training, but earlier than some ranked development games. This is **retrospective validation, not forward-time validation after model selection**. The strict future-test attempt found no completed matches beyond its boundary; its empty manifest and policy are preserved. No test scores were used to switch recipes.

## Independent historical results

MAE is measured in each target's own units; lower is better. All 1,000 players belong to the same 100 complete matches for both predictors. Confidence intervals use 2,000 paired bootstrap resamples of whole matches, seed 20261117. Intervals are per target and are not adjusted for multiple comparisons.

| Target | Existing Ridge | Ranked predictor | Relative MAE reduction | New minus old MAE, 95% CI |
| --- | ---: | ---: | ---: | --- |
| Last hits | 18.719 | 17.827 | 4.8% | −0.892 [−1.514, −0.244] |
| Gold | 760.904 | 740.395 | 2.7% | −20.510 [−40.950, +0.078] |
| XP | 1,156.381 | 1,132.144 | 2.1% | −24.236 [−53.504, +5.100] |

The release criterion, fixed before opening this test, required at least 50 unseen games and lower point MAE for every target. It passed. **Only last-hit improvement has a paired interval entirely below zero.** Gold and XP improvements remain uncertain. XP bias worsened from −66.97 to −223.36 despite lower absolute error; the model still systematically underpredicts some growth. All slices and worst errors are retained in the JSON report.

The descriptive 90th-percentile bands were estimated on ranked development residuals. On this test they covered 91.2% of last-hit, 90.3% of gold and 90.4% of XP outcomes. They are not independently calibrated conformal intervals or guarantees. A shortfall is a reason to inspect a replay, not proof of a player mistake.

## Reproduction

Run from the repository root using Python 3.14 for the pinned experiment environment:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-ml.txt
.\.venv\Scripts\python -m pro_replay benchmark-coach --stage select --out data/ml
.\.venv\Scripts\python -m pro_replay benchmark-coach --stage evaluate --out data/ml
.\.venv\Scripts\python -m pro_replay benchmark-coach --stage ranked-select --out data/ml
.\.venv\Scripts\python -m pro_replay benchmark-coach --stage ranked-evaluate --out data/ml
.\.venv\Scripts\python -m pro_replay benchmark-items --out data/item-comparison.json
.\.venv\Scripts\python -m unittest discover -s tests -v
```

These commands use bundled snapshots without network access. Professional candidate training took 149 seconds on the recorded machine with one CPU thread; this is an observation, not a runtime guarantee. Seeds, versions, candidate hashes, split IDs and selection receipts are retained. `scripts/rebuild_ml_snapshots.py` optionally rebuilds data from raw OpenDota cache; changing upstream responses must not silently replace frozen evaluation data.

Tests cover future-label isolation, grouped temporal boundaries, frozen protocols, dataset hashes, previously evaluated IDs, portable predictor parity with scikit-learn, cohort routing and reproduction of released error metrics. GitHub Actions runs the suite with the pinned ML environment.

## Limits and next acceptance criteria

- The final sample occupies about ten minutes on one patch and overrepresents high-rated lobbies. All matches are parsed, complete, at least twenty minutes long and have no recorded disconnect. Other populations are not established.
- Fifteen of the 1,000 test players appeared in previous matches; account IDs are not features. The bootstrap models within-match dependence, not every possible player or time dependency.
- The next prospective comparison must use the preserved later-time policy and fixed model, followed by different patches and broader time windows. Gold/XP gains need more independent evidence.
- The separate item experiment remains at 12/20 (60%), tied with the original model. Unseen/rare heroes dominate its errors. [Item model card](item-model-card.md).
- Fighting advice still combines replay checks and rules. Expert-labelled action quality, replay-state coverage, advice acceptance and improvement in client performance need their own evaluation. Resource prediction accuracy is not evidence that a suggested focus or item is optimal.
