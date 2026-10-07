---
name: compare-embeddings
description: >-
  Build one interactive page that plots cell embeddings from Helical runs as UMAPs, one view
  per model, coloured by datasource. Use when the user wants to plot, visualize, explore or
  compare embeddings, see a UMAP of a finished embedding run, or see whether a model mixes
  or separates several datasets. To produce embeddings in the first place, use
  compute-embeddings.
---

# Compare embeddings: one interactive page

A bundled script turns a jobs file into the page: it validates the file, computes a UMAP
per model and writes the result into `assets/template.html`. **You never write HTML or
handle coordinates.** You download the embeddings, write the jobs file, run one command and
relay the summary it prints.

## 1. Collect the runs

`listDagRuns({ dagIds: "embedding", state: "succeeded" })`, then `getRunDetails({ runId })`
for each (in parallel). Each run gives a model (`base_model`), a dataset
(`used_dataset.dataset_id`, named by `listDatasets`) and an `embedding` artifact.

Keep the newest run per model and dataset unless the user asks for repeats: repeat runs are
often identical.

## 2. Download the embeddings

`downloadArtifact({ artifactId })` returns a URL; save it with
`curl -sS -o <dir>/<file>.npy '<url>'`. URLs expire within an hour, so download right away,
and do not repeat them in the chat. A **404** means the file is gone: leave that run out
and tell the user.

## 3. Write the jobs file

```json
{"models": {
  "scGPT": {"embeddings": [
    {"datasource": "evotoken_L5_97aff2", "path": "emb/scgpt_evotoken.npy"},
    {"datasource": "GSE205013 PDAC", "path": "emb/scgpt_pdac.npy"}]},
  "Geneformer 12L": {"embeddings": [
    {"datasource": "evotoken_L5_97aff2", "path": "emb/gf12_evotoken.npy"}]}
}}
```

- **Model names are yours to choose**: the user's wording, else the run's `base_model`, or
  the registered name of a fine-tuned model. They are only labels.
- **`datasource`** is the dataset's name. Use the same spelling in every model, so each
  datasource gets the same colour in every view.
- A model's embeddings are plotted together, so they must have the same width. Relative
  paths are relative to the jobs file. Unknown fields are rejected.

## 4. Build

```sh
uv run <skill dir>/scripts/build_comparison.py jobs.json --out explorer.html --target artifact
```

- `uv run` installs numpy, pydantic, scikit-learn and umap-learn on first use. Without `uv`,
  `pip install` those four into a virtual environment and run it with `python`.
- About 25 s of fixed startup (imports and compiling UMAP), then about 5 s per model of
  4,000 cells, growing with the cell count. The page costs about 40 bytes per cell per model.
  Above roughly 100,000 cells in total, tell the user the wait first. A Claude artifact holds at most
  16 MB, about 400,000 cell-model points.

## 5. Deliver

| You have | Build with | Then |
|---|---|---|
| The Artifact tool | `--target artifact` | Publish the file and give the link |
| No Artifact tool | `--target file` (default) | Give the path; open it with `xdg-open`/`open` for a local user |

The page loads Plotly from cdnjs, so the viewer needs internet access. Relay the printed
summary: cells per datasource and, for models with several datasources, how often a cell's
neighbours come from its own datasource compared with chance. Close to chance means the
model mixes the datasources; close to 100% means it keeps them apart. Pass on any
IDENTICAL warning. Do not say you looked at the plot; you saw the summary.
