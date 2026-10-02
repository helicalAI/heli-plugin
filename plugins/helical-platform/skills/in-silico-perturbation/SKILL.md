---
name: in-silico-perturbation
description: >-
  Run an in-silico gene knockout, knockdown or overexpression screen with Helical
  foundation models by editing the expression matrix locally, then embedding the
  perturbed cells on the platform. Use when the user wants to perturb genes, screen
  targets, or find genes whose perturbation shifts diseased cells toward a healthy state
  — even when no perturbation tool is available, because none is needed.
---

# In-silico perturbation

Finds which gene perturbations move cells from one state toward another, by showing a
foundation model perturbed cells and measuring where their embeddings land.

**The one thing to understand before starting:** a perturbation is an edit to the
expression matrix, and you can make it yourself. You do not need a perturbation, target-ID
or gene-validation tool, and their absence is not a reason to stop. The work splits in
two:

- **Locally:** download the dataset, edit the counts, build one file, and later score the
  embeddings.
- **On Helical:** upload that file, embed it with each model, download the embeddings.

**The model must see the perturbed counts.** Embedding the unperturbed dataset and then
"knocking down" a gene in embedding space, or scoring genes against centroids of the
original embeddings, measures nothing. Every perturbed condition goes through the model.

## 1. Get the expression matrix locally

`listDatasets(...)` → `getDataset({ id })`. Note `cellCount`, `geneCount`, and the
`.obs` columns it names: `celltypeColumn`, `diseaseColumn`, `donorColumn`. You need the
cell-type and state columns to choose cells and to score.

The platform does not hand back a dataset's own file. Get the `.h5ad` from the record's
`linkData` (the original download URL), or from the user if they have it. If neither
exists, say so plainly; do not substitute a different dataset.

Check before editing:

- **Raw counts.** The models normalise their input themselves. If `X` holds normalised or
  log values, use `layers["counts"]` or `raw` instead; if there are no counts, tell the
  user the perturbation will be less faithful.
- **Genes are present and expressed.** For each candidate, report the fraction of target
  cells with non-zero counts. Drop genes that are absent or barely detected — knocking out
  a gene the cells do not express changes nothing and wastes a slot.

## 2. Build one combined file

Put every condition in **one `.h5ad`**, so each model needs one embedding run, not one per
gene, and every condition goes through the same model pass. Tag each cell with a
`perturbation` column in `.obs`:

| Block | Cells | `perturbation` |
|---|---|---|
| Reference | target-state cells (e.g. healthy), unedited | `reference` |
| Control | source-state cells (e.g. injured), unedited | `control` |
| One per gene | a copy of the control cells with that gene edited | the gene name |
| Null | a copy of the control cells with a random, expression-matched gene edited | `null_<gene>` |

- **Include the null.** Any edit moves an embedding a little. Without 3–5 random genes,
  matched to the candidates on detection rate and edited the same way, there is nothing
  to say a candidate's shift is more than noise.
- **Subsample.** The file holds `reference + control × (1 + genes + nulls)` cells. A few
  hundred control cells per cell type is enough; balance donors where the metadata allows.
- **Keep the layout of the source.** Change only count values and add the `perturbation`
  column. Keep `.var` and the existing `.obs` columns as they were, so the same gene
  identifiers and column names apply. Give each copied cell a unique `obs_names`, such as
  `<cell>__<perturbation>`.

How to edit a gene, on raw counts:

- **Knockout:** set the gene to 0.
- **Knockdown:** multiply its counts by a factor, e.g. 0.25. Geneformer reads gene *rank*,
  so a partial knockdown may not change its input at all — use a knockout for it, and say so.
- **Overexpression:** raise it, e.g. to its 99th percentile across the dataset.

State the edit you chose; it is your decision, not a platform default.

## 3. Price it, then confirm

Price before uploading anything: the cost depends on the combined file's cell count, so
work it out with the rates from `listModels` as `check-credits` describes. Each model is
**one job** over the whole combined file. Show the user the cell count, the per-model
estimate and the total, and get their yes before spending.

If the total is too much, cut cells or genes; do not split into per-gene runs, which pays
the per-job amount once per gene.

## 4. Upload and register

`initiateDatasetUpload({ sizeBytes })` → upload every part → `completeDatasetUpload` →
`registerDataset`. Follow the tool descriptions exactly: slice parts by **exact bytes**
from each part's `offsetBytes` and `lengthBytes`, and do not complete while a part is
outstanding.

Register with the source record's metadata, a new `name` that says what was perturbed
(names are unique per project; a clash is a 409), and `parentDatasetId` set to the source
dataset, so the derivation is on record.

## 5. Embed with each model

Follow `compute-embeddings` for each model on the registered dataset: queue the run, show
the platform's own quote, and approve only after the user says yes. That quote replaces
your estimate.

## 6. Download and score

`getRunDetails({ runId })` until `succeeded`, then `downloadArtifact({ artifactId })` for
the embedding matrix and fetch it with `curl`. If the artifact carries cell ids, join on
them. If it is a bare matrix, check its row count equals your file's cell count before
assuming the rows follow your file's order, and say you assumed it.

Score **within each cell type**, using cosine distance in each model's own space:

- `H` = centroid of the `reference` cells of that type.
- For each control cell `i` and condition `g`:
  `shift = (d(controlᵢ, H) − d(gᵢ, H)) / d(controlᵢ, H)`. Positive means toward the
  reference.
- A gene's score is its mean shift, compared with the null genes' shifts: report its
  margin over the null, not the raw number.
- **Identity check:** a perturbed cell should still be nearest its own cell type's
  centroid. A gene that "improves" cells by making them stop looking like their cell type
  is a red flag, not a hit.

Across models, a gene matters when it beats the null in at least two of them.

## 7. Report

A ranked table per cell type: gene, mean shift per model, margin over the null, models in
agreement, identity check passed or not. Lead with the genes that agree across models.

These are hypotheses for experimental follow-up, not validated targets. Say what was
edited and how, how many cells, and any assumption about row order.

## Hand-off

- Embedding mechanics and approval → `compute-embeddings`.
- Cost and balance → `check-credits`.
- Want a model adapted to the tissue first? → `fine-tune-model`, then embed with it here.

## Conventions

Edit counts locally, never embeddings · the model sees every condition · one combined file,
one run per model · always include an expression-matched null · score within cell type
against the reference centroid · check identity is preserved · estimate → show the price →
explicit yes → run · hypotheses, not hits · never print credentials or raw upstream errors.
