# Error analysis: professional games do not fully represent ranked clients

Phase 1 selected `ridge_rich_1` using professional-game validation. It improved professional validation scores but **failed the ranked release gate on all three targets**. The evidence supports a mismatch between training and client populations, with systematic farm-rank errors and brittle hero effects. It does not prove a single causal explanation for every error.

Sources: [phase-1 comparison](../examples/economy-model-comparison.json), [additional numerical evidence and replay excerpt](../examples/ml-error-analysis-evidence.json), [item comparison](../examples/item-model-comparison.json). This analysis uses phase-1 observations only. Models, recipes and thresholds were not changed for this analysis.

## 1. The validation population mattered

MAE measures error in the predicted 20-minute total, using observations available at 10:00. Lower is better. The three targets are last hits, cumulative gold and XP; they are not player-impact percentages.

| Evaluation | Model | LH MAE | Gold MAE | XP MAE |
|---|---|---:|---:|---:|
| Professional rolling validation, 240 games | Existing ridge | 14.25 | 620.60 | 961.90 |
| Same professional validation | Selected rich ridge, alpha 1 | **13.04** | **594.77** | **926.97** |
| Earlier ranked benchmark, 119 games | Existing ridge | **19.69** | **764.20** | **1133.87** |
| Same earlier ranked benchmark | Selected rich ridge, alpha 1 | 20.71 | 804.21 | 1176.26 |
| Phase-1 fresh ranked evaluation, 100 games | Existing ridge | **20.05** | **836.23** | **1215.13** |
| Same phase-1 ranked evaluation | Selected rich ridge, alpha 1 | 21.31 | 869.71 | 1245.23 |

On the phase-1 fresh ranked set, selected-minus-existing MAE was +1.26 LH, +33.49 gold and +30.10 XP. Match-grouped 95% bootstrap intervals were respectively **[+0.70, +1.82]**, **[+12.70, +54.59]**, and **[+3.38, +55.98]**. This was not merely a failure to find a significant benefit: all three estimated errors increased. These intervals concern the fixed phase-1 comparison; they do not protect later selection among candidates from evaluation reuse.

The fresh professional sample contained only five games and cannot establish broad professional generalization. The earlier 100-game professional benchmark showed smaller gains than cross-validation: −0.25 LH, −13.10 gold and −23.78 XP versus the existing model, with the LH and gold intervals crossing zero.

## 2. Relative farm rank shifted between populations

Training used 400 professional matches, or 4,000 player rows. The phase-1 ranked evaluation had 100 matches and 1,000 rows. Mean last hits at 10:00 fell from **40.36 to 30.87**; mean team gold at 10:00 fell from **15,446 to 14,460**. The audited numeric features were present in both sets, so their distribution shift is not explained by missing-value imputation.

`farm_rank_10` ranks a player by gold within their own team at 10:00. It is **not a verified Dota position label**. Its association with subsequent farm was different:

| Gold rank within team at 10:00 | Pro training: mean LH gained 10–20 | Ranked: mean LH gained 10–20 | Ranked selected-model LH bias | Ranked gold bias |
|---|---:|---:|---:|---:|
| 1 | 121.06 | 86.55 | +15.73 | +157.57 |
| 2 | 106.22 | 81.17 | +17.81 | +349.71 |
| 3 | 101.14 | 67.55 | **+25.78** | **+703.23** |
| 4 | 18.23 | 25.14 | **−7.48** | **−424.74** |
| 5 | 15.77 | 16.45 | +0.92 | −164.16 |

Each ranked row above covers 200 players across 100 matches. Bias is prediction minus observation: positive means overprediction. At rank 3, the professional training mean gold gain was 5,792.54, compared with 4,794.77 in ranked games; rank 4 changed in the opposite direction, 2,896.20 to 3,395.60.

These are correlated observational slices, not randomized comparisons. They support the hypothesis that a sharp professional core/support allocation transfers poorly through a relative-gold proxy. The overall gold bias of only +124.32 conceals substantial and opposing rank-specific errors. Advice based on a single global residual threshold would miss that distinction.

## 3. Rare heroes were more common, but unseen heroes were not the whole problem

Players on heroes with 1–19 training examples increased from **420/4,000 (10.5%)** in training to **434/1,000 (43.4%)** in the ranked evaluation. Only three ranked players had entirely unseen heroes. Gold MAE was 879.02 on the rare-hero slice and 864.02 on heroes with at least 20 examples. Large errors therefore also occurred on familiar heroes; unsupported heroes alone cannot explain the transfer failure.

A coefficient diagnostic offers a plausible reason stronger regularization might transfer differently. After converting coefficients back into target units, the norm of the hero coefficients changed as follows:

| Target | Rich ridge alpha 1 | Same features, alpha 100 |
|---|---:|---:|
| Last hits | 225.98 | 40.04 |
| Gold | 6,820.86 | 1,278.62 |
| XP | 8,856.80 | 1,630.73 |

For example, the alpha-1 model had a +57.1 LH coefficient for hero 82 from only two training examples. Coefficients are conditional components of a correlated linear model, **not causal hero bonuses**. The stronger penalty also changes other coefficients, so these norms do not isolate a causal mechanism.

The already-frozen alpha-100 candidate had ranked MAEs of 18.52 LH, 789.26 gold and 1176.83 XP, while performing worse on professional validation. This ranking reversal is diagnostic evidence that the model-selection population matters. It does not authorize choosing alpha 100 on these same rows and calling them an untouched final test. Once reused for selection, these ranked games become development data; a separately frozen evaluation is required. Historical independent evaluation and a genuinely future evaluation must be reported separately.

## 4. Error bands also failed to transfer

The selected model's descriptive 90th-percentile error bands came from professional validation. On phase-1 ranked games they covered only **70.5% of LH**, **76.2% of gold**, and **79.7% of XP** outcomes. Their coverage on the earlier professional benchmark was 87.5%, 88.0% and 88.9%.

These bands were never independently calibrated guarantees. Their degraded ranked coverage confirms that professional residual distributions should not be presented as reliable client confidence intervals. A large deviation is a review candidate, not proof that a client made a mistake.

## 5. A concrete large error: Ursa, match 9094347773

In [this match](https://www.opendota.com/matches/9094347773), the selected model predicted **8,595.62 gold at 20:00**, while the observed total was **4,640**: an overprediction of 3,955.62. Ursa had 2,641 gold and 40 last hits at 10:00, increasing to only 58 last hits by 20:00.

The cached OpenDota death log records seven deaths at **10:13, 11:01, 12:23, 13:12, 14:43, 15:46 and 16:41**. Their recorded dead time totals **194 seconds**, about 32.3% of the ten-minute prediction window. There were no recorded kills in that window. The evidence JSON preserves the relevant source entries and a hash of the cached response without account identifiers.

This sequence makes the farming shortfall understandable after the match. Those deaths had not happened at prediction time and were not inserted into the model's inputs. We cannot infer that all 3,955.62 gold was avoidable, identify the correct focus target, or assign blame from these observations. Such advice needs replay state, fight context and an independently evaluated decision model.

## 6. Item prediction had a different failure pattern

The Yatoro experiment used 100 professional matches with earlier temporal validation and a previously reported 20-match test. Both the selected hero-frequency baseline and the original softmax predicted **the same 12/20 matches correctly**. A 95% Wilson interval for that 60% accuracy is 38.7%–78.1%.

- Five of eight errors were on heroes absent from training: Clinkz three times, Windranger and Necrophos.
- Two more were on heroes with only one or two examples: Weaver and Sven.
- The remaining error was Morphling with four training examples, where Shard appeared instead of predicted Manta.

Unlike the economy experiment, sparse hero coverage directly accounts for most observed item errors. The unsmoothed baseline gave probability 1.0 to the wrong Weaver and Sven predictions. Its test log-loss was 4.957, versus 1.655 for the smoothed hero prior, despite identical top-1 accuracy. Runtime output now exposes training support, unseen heroes and `probability_is_calibrated: false`; the detailed [item model card](item-model-card.md) documents all nine recipes and the corrected patch-version export.

The result is an explicit boundary on the product claim: economy forecasting and observed-item imitation can guide review, but neither currently measures causal player impact or proves which item or action would have won a fight.
