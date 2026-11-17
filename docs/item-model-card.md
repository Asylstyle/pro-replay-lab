# Recorded next-item experiment

The experiment found **no demonstrated top-1 accuracy improvement over the existing hero baseline**. The selected recipe and the original softmax both correctly predict 12 of the same 20 benchmark matches. This is a useful negative result: a more complicated classifier is not justified by this sample.

## Task and data

Predict the first **recorded tracked item** after 10:00 and through 30:00 from the hero, patch and LH/gold/XP at exactly 10:00. One logistic variant additionally sees tracked purchases at or before 10:00. A purchase prediction describes what the player did; it is not an optimal-build recommendation, a chance of winning, or a counterfactual fight policy.

- 100 professional matches of Yatoro, account `321580662`, across 28 heroes and six league IDs, July 10–November 16, 2026. All 100 have the required observations.
- OpenDota patch ID `60` spans the release-date-derived versions `7.41d`, `7.41e`, `7.41f` in this archive.
- Dataset: [item-training-snapshots.json.gz](../examples/item-training-snapshots.json.gz), including match IDs, league IDs, snapshots, item events and patch-join provenance.
- Earlier 80 matches are development data. Expanding folds use 40→20 and 60→20 matches. Same start timestamps never straddle a split. Scaling, categories, history vocabulary and classifier parameters are fitted separately within each training fold.
- Recipes were fixed before scoring the last 20; selection maximizes pooled validation accuracy, breaking ties by validation log-loss then recipe name. The last 20 were already used in the original model report, so **this is a legacy benchmark, not a newly sealed holdout**.

## Comparison

| Recipe | Validation accuracy | Test accuracy | Test macro F1 | Test top-3 | Test log-loss |
|---|---:|---:|---:|---:|---:|
| Global prior | 15.0% | 5.0% | .010 | 30.0% | 2.747 |
| Hero prior, smoothing 1 | 60.0% | 60.0% | .517 | 65.0% | 1.655 |
| Hero prior, smoothing 5 | 52.5% | 60.0% | .517 | 65.0% | 1.827 |
| Original softmax SGD | 40.0% | 60.0% | .505 | 65.0% | 1.779 |
| Logistic, C=.1 | 22.5% | 25.0% | .262 | 45.0% | 2.478 |
| Logistic, C=1 | 45.0% | 60.0% | .520 | 60.0% | 1.831 |
| Logistic with pre-10 history | 45.0% | 60.0% | .480 | 60.0% | 1.751 |
| Small random forest | 35.0% | 40.0% | .411 | 55.0% | 2.249 |
| **Hero frequency, selected by validation** | **60.0%** | **60.0%** | **.517** | **70.0%** | **4.957** |

The selected recipe won the prespecified tie-break on validation log-loss (5.335 versus 5.551 for smoothing 1). Its much worse test log-loss shows how brittle unsmoothed frequencies are. We keep that selection visible; we do not change the selection rule after seeing test outcomes. Seven of 40 validation labels were absent from their fold's training vocabulary; their probability floor is `1e-12`, explicitly included in log-loss.

The 95% Wilson interval for 12/20 accuracy is **38.7%–78.1%**. The selected model and original softmax have identical top-1 correctness per match; the paired difference is zero. Neither this tie nor the small top-3 difference establishes superior performance on future matches. Macro F1 uses the union of observed and predicted classes in each scoring set.

The JSON report also contains Brier score, five-bin calibration error, confidence-bin counts, per-item F1, threshold coverage, split IDs and paired bootstrap results. These are descriptive diagnostics on a small sample, not a calibrated confidence guarantee.

## What failed

Eight selected-model errors:

| Situation | Errors | Observed purchase versus predicted purchase |
|---|---:|---|
| Clinkz, absent from training | 3 | Aghanim's Shard versus Manta |
| Windranger, absent from training | 1 | Maelstrom versus Manta |
| Necrophos, absent from training | 1 | Radiance versus Manta |
| Weaver, one training match | 1 | Dragon Lance versus Manta |
| Sven, two training matches | 1 | Echo Sabre versus Mask of Madness |
| Morphling, four training matches | 1 | Aghanim's Shard versus Manta |

Five errors came from unseen heroes and two more from heroes with only one or two examples. Sparse hero coverage is the principal observed failure, not a reason to add larger models. The unsmoothed baseline reports probability 1.0 for the Weaver and Sven mistakes. Every runtime prediction therefore includes `probability_is_calibrated: false`, the hero's training support and an unseen-hero flag; probabilities must not be presented as reliable confidence. Match IDs and OpenDota links for every error are in the report.

Next experiments should collect more independent examples for underrepresented heroes and evaluate on newly frozen future matches. Role coverage, player transfer, patch transfer and item recommendation quality require separate evaluations. The current classifier has no enemy-draft representation or expert labels of an optimal item.

## Reproduction and artifacts

- [Complete comparison and failure analysis](../examples/item-model-comparison.json)
- [Portable selected model](../examples/yatoro-selected-model.json), fitted on the same earlier 80 matches, without refitting on the test set
- `pro_replay.item_experiments.run_item_experiment(rows)` returns a `report` and JSON-only `model`; rows are under `rows` in the gzip dataset.
- Training uses the optional scikit-learn environment, seed 41 and one thread. Inference uses only the standard library, with no pickle loading.
- Tests cover temporal boundaries, target leakage, holdout-independent model selection, missing data, unseen heroes and labels, mixed-account rejection, and portable logistic/forest probability parity against scikit-learn.

During initial reproduction, direct SQL export omitted `Archive.rows()`'s release-date join for `patch_version`; this made the reconstructed original softmax score 50%. The export was corrected using the unchanged original `valve_patches` table, restoring the exact saved 60% result. Candidate recipes were not changed after that diagnostic run. The compact dataset preserves the join and its original release dates so this error cannot silently recur.
