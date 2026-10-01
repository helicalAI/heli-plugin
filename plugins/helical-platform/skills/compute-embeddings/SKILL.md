---
name: compute-embeddings
description: >-
  Compute cell embeddings for a single-cell dataset with a Helical foundation model, then
  locate the outputs. Use when the user wants embeddings, representations, or features
  from a dataset already in the platform catalogue. For training a model on labelled data,
  use fine-tune-model instead.
---

# Compute embeddings

Turns a catalogue dataset plus a foundation model into an embedding matrix and a UMAP.

**The one thing to understand before starting:** `start_embedding_run` consumes real
compute and starts immediately. Nothing downstream will ask the user to confirm — there is
no approval screen for the user to click through. **You are the
confirmation step.** State the dataset, the model, and what the run will do, and get an
explicit yes before calling it.

You also cannot choose a project: scope comes from the signed-in user, and no tool accepts
a project or conversation identifier.

## 1. Select the dataset

`list_datasets({ name?, organism?, tissue?, disease?, limit? })` — `name` and `author`
match as case-insensitive substrings; `organism`, `tissue` and `disease` match an exact
element of the dataset's array field, so `organism: "human"` only matches if that exact
string is present. Note `total` is the count across all pages, not the rows returned.

`get_dataset({ id })` for the full record, including `cellCount` and `geneCount` — worth
reporting, because run time scales with them.

There is **no upload tool**: datasets must already exist in the catalogue. If the user has
their own `.h5ad`, say plainly that this surface cannot ingest it and that they need to
add it through the Helical console.

## 2. Choose a model

`list_models({ modelType?, status? })` — promoted models by default; pass
`status: "all"` to see unpromoted candidates too. Pass the returned **`name`** as `model`.

A bare base-model name is accepted only if it is a known identifier (`scgpt`,
`gf-12L-40M-i2048`, `tf_sapiens`, `helix-mRNA`, and similar). For a fine-tuned model use
its registered `<base>_v<n>` name, or give `model` plus `model_version`. An unrecognised
bare name is rejected with a 400 rather than guessed at, and a promoted model belonging to
another workspace returns 403.

## 3. Price it, then confirm, then run

```
estimate_embedding_run({ datasetId, model, batch_size, modalities?, model_version? })
```

Free, starts nothing, safe to call repeatedly. Returns the token count, the price, the
assumptions behind it, and a `quote_id`. **Show the user the tokens and the price**, and
get an explicit yes. Then:

```
start_embedding_run({ ...the same arguments, quote_id })
```

The quote is what fixes the price, and the run tool will not accept a call without one.
Change any argument and the quote no longer applies — estimate again.

- **`batch_size` is required.** The API's own description says it can be omitted; it
  cannot — omitting it is a 400. 8–32 is a reasonable starting range.
- **`modalities` must include `"sc"` for TranscriptFormer models** (`tf_sapiens`,
  `tf_metazoa`, `tf_exemplar`), which fail at run time without it. For other single-cell
  models it can be omitted.

Do not batch several runs behind one confirmation, and do not treat an earlier "sounds
good" about the plan as approval for the run itself.

Each call starts a separate run: a retry after a timeout may duplicate work, so check
`list_runs` before re-issuing.

## 4. Follow it through

`list_runs({ dagIds: ["embedding"], state: ["running"] })` to find the run, then
`get_run_details({ runId })` until it reaches a terminal state. Runs take minutes to hours;
poll at a sensible interval and keep the user informed rather than going silent.

## 5. Report the outputs

`get_run_details({ runId })` returns `artifacts[]` with `artifact_type`, `display_name`
and `s3_key`.

- `list_files({ path })` on the run's output directory to see what was produced.
- `list_s3_files({ path? })` lists an object-storage prefix **non-recursively**, across the
  caller's projects. Use it when an `s3_key` from `artifacts[]` needs to be located or
  confirmed to exist; use `list_files` for walking a run's output directory.
- `read_file({ path, maxBytes? })` reads **UTF-8 text only, up to 1 MiB**. An embedding
  matrix is a binary `.npy` — it cannot be read through this tool. Report its path and let
  the user fetch it from the Helical console; do not pretend to have inspected it.
- For the UMAP, `list_umaps({ datasetId })` then `get_umap({ runId })` returns parsed
  coordinates and labels. The payload can be very large: summarise it, do not echo it.

## Hand-off

- Want the model trained on labels first? → `fine-tune-model`, then return here.
- Judging whether an embedding is good — separation of the conditions of interest in the
  UMAP versus a zero-shot baseline — is interpretation, not something these tools report.

## Conventions

`datasetId`, never a path · scope is derived from the signed-in user, you cannot pass a
project · estimate → show the price → explicit yes → run with the quote · poll the run · read only under
`/projects/<project>/data` · never print credentials or raw upstream errors.
