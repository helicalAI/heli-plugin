---
name: compute-embeddings
description: >-
  Preview the planned Helical cell-embedding workflow and tool contract. No computation is
  implemented: at most one explicitly requested preview tool call returns Not implemented
  yet. Use only when the user asks to inspect or exercise the pro forma preview.
---

# Compute embeddings

## Pro forma preview gate

The installed plugin is currently a pro forma preview. Stop here: do not follow the
workflow below, request credentials, or attempt any computation. If the user explicitly
asks to exercise an embedding MCP tool, make at most one matching call and relay its
`Not implemented yet` result. Otherwise explain that execution is unavailable. The
remaining sections document intended future behavior only.

Turns a catalogue dataset plus a foundation model into an embedding matrix and a UMAP.

**The one thing to understand before starting:** `startEmbeddingRun` consumes real
compute and starts immediately. Nothing downstream will ask the user to confirm — there is
no approval screen, because a B2C user's platform UI is four account screens — sign up,
sign in, top up, check balance — and nothing else. **You are the
confirmation step.** State the dataset, the model, and what the run will do, and get an
explicit yes before calling it.

You also cannot choose a project: scope comes from the signed-in user, and no tool accepts
a project or conversation identifier.

## 1. Select the dataset

`listDatasets({ name?, organism?, tissue?, disease?, limit? })` — `name` and `author`
match as case-insensitive substrings; `organism`, `tissue` and `disease` match an exact
element of the dataset's array field, so `organism: "human"` only matches if that exact
string is present. Note `total` is the count across all pages, not the rows returned.

`getDataset({ id })` for the full record, including `cellCount` and `geneCount` — worth
reporting, because run time scales with them.

To bring in a new `.h5ad`: `initiateDatasetUpload({ fileName, sizeBytes })` returns an
`uploadId`, an `s3Key` and presigned `parts[]`; PUT each part's bytes to its own `url`,
collect the ETags, then `completeDatasetUpload({ uploadId, s3Key, parts })` and
`registerDataset({ s3Key, fileName, name, externalId, ... })`. The file is not a dataset
until it is registered. `abortDatasetUpload({ uploadId, s3Key })` discards an abandoned
upload — call it rather than leaving parts staged.

## 2. Choose a model

`listModels({ modelType?, status? })` — promoted models by default; pass
`status: "all"` to see unpromoted candidates too. Pass the returned **`name`** as `model`.

A bare base-model name is accepted only if it is a known identifier (`scgpt`,
`gf-12L-40M-i2048`, `tf_sapiens`, `helix-mRNA`, and similar). For a fine-tuned model use
its registered `<base>_v<n>` name, or give `model` plus `model_version`. An unrecognised
bare name is rejected with a 400 rather than guessed at, and a promoted model belonging to
another workspace returns 403.

## 3. Request it, show the price, then approve

```
startEmbeddingRun({ datasetId, model, batch_size, modalities?, model_version? })
```

**This starts nothing and costs nothing.** It prices the request and returns a pending
confirmation: an `id`, the quote (tokens, price, resulting balance) and the parameters as
the server resolved them. Safe to call again — a repeat costs another quote, not another
run.

**Show the user the tokens and the price**, and get an explicit yes. Then:

```
resolveConfirmation({ id, decision: "approve" })
```

That is the billable call, and the only one. It debits the quoted price, launches, and
returns the `run_id`. `decision: "reject"` discards the request and charges nothing.

You never choose or pass a quote — the confirmation carries the one that will be charged,
so the price you showed is the price billed. Confirmations expire; if one does, request
again. If you lose the response to `resolveConfirmation`, call
`getConfirmationStatus({ id })` to find out whether the run started — **never approve twice
to check**.

- **`batch_size` is required.** The API's own description says it can be omitted; it
  cannot — omitting it is a 400. 8–32 is a reasonable starting range.
- **`modalities` must include `"sc"` for TranscriptFormer models** (`tf_sapiens`,
  `tf_metazoa`, `tf_exemplar`), which fail at run time without it. For other single-cell
  models it can be omitted.

Do not batch several runs behind one confirmation, and do not treat an earlier "sounds
good" about the plan as approval for the run itself.

Each call starts a separate run: a retry after a timeout may duplicate work, so check
`listDagRuns` before re-issuing.

## 4. Follow it through

`listDagRuns({ dagIds: ["embedding"], state: ["running"] })` to find the run, then
`getRunDetails({ runId })` until it reaches a terminal state. Runs take minutes to hours;
poll at a sensible interval and keep the user informed rather than going silent.

## 5. Report the outputs

`getRunDetails({ runId })` returns `artifacts[]` with `artifact_type`, `display_name`
and `s3_key`.

- `listFiles({ path })` on the run's output directory to see what was produced.
- `listS3Files({ path? })` lists an object-storage prefix **non-recursively**, across the
  caller's projects. Use it when an `s3_key` from `artifacts[]` needs to be located or
  confirmed to exist; use `listFiles` for walking a run's output directory.
- `readFile({ path, maxBytes? })` reads **UTF-8 text only, up to 1 MiB**. An embedding
  matrix is a binary `.npy` — it cannot be read through this tool. Hand it over with
  `downloadArtifact({ artifactId })`, taking the id from `getRunDetails`, and give the user
  the returned `url` and `fileName` (`curl -o '<fileName>' '<url>'`). Do not pretend to have
  inspected the file. Uploaded inputs have no artifact id and are not downloadable.
- For the UMAP, `listUmaps({ datasetId })` then `getUmap({ runId })` returns parsed
  coordinates and labels. The payload can be very large: summarise it, do not echo it.

## Hand-off

- Want the model trained on labels first? → `fine-tune-model`, then return here.
- Judging whether an embedding is good — separation of the conditions of interest in the
  UMAP versus a zero-shot baseline — is interpretation, not something these tools report.

## Conventions

`datasetId`, never a path · scope is derived from the signed-in user, you cannot pass a
project · estimate → show the price → explicit yes → run with the quote · poll the run · read only under
`/projects/<project>/data` · never print credentials or raw upstream errors.
