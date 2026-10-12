---
name: compute-embeddings
description: >-
  Compute cell embeddings for a single-cell dataset with a Helical foundation model, then
  download the outputs. Use when the user wants embeddings, representations, or features
  from a dataset already in the platform catalogue. For training a model on labelled data,
  use fine-tune-model instead.
---

# Compute embeddings

Turns a catalogue dataset plus a foundation model into an embedding matrix.

**The one thing to understand before starting:** `triggerEmbedding` does not start
anything. It queues the run and returns a `confirmation_id` and the platform's price. The
run starts only when `resolveConfirmation` approves it, and **that call is the user's
approval**. State the dataset, the model and the price, and get an explicit yes before
making it.

You also cannot choose a project: scope comes from the signed-in user, and no tool accepts
a project or conversation identifier.

## 1. Select the dataset

`listDatasets({ name?, author?, organism?, tissue?, disease?, dataPath?, limit? })`. The
filters combine. `name`, `author` and `dataPath` match a case-insensitive substring;
`organism`, `tissue` and `disease` match an exact element of the dataset's array field, so
`organism: "human"` only matches if that exact string is present. `limit` defaults to 10,
at most 20. `total` counts every match, not just the rows returned.

`getDataset({ id })` for the full record, including `cellCount` and `geneCount`. Report
them: run time and price scale with the cell count.

A user's own `.h5ad` can be added with `initiateDatasetUpload` → `completeDatasetUpload`
→ `registerDataset`; follow those tools' descriptions for splitting the file. Once
registered, it is embedded like any catalogue dataset.

## 2. Choose a model

`listModels()`. It returns two lists:

- `base_models`: foundation models. Pass the `name` as `model` and omit `model_version`.
- `models`: this workspace's fine-tuned models, promoted ones by default. Pass the `name`
  as `model` and the `version`, as a number, as `model_version`. Never pass `base_model`.

Only single-cell models are listed, and only those can run here. Every entry carries its
rates, `credits_per_job` and `credits_per_cell`. Pick one whose `credits_per_cell` is not
`null`; an unpriced model cannot run here.

Only promoted fine-tunes can launch. `listModels({ status: "unpromoted" })` shows the
others, still with a price, but a launch with one is refused with a 400 saying there is no
cell price for the model. That means it is unpromoted, not unpriced. Do not retry it, and
do not relay the raw message.

## 3. Queue it, show the price, then approve

```
triggerEmbedding({ datasetId, model, model_version? })
```

Send only these fields (and `pretrained_embedding_species`, if the user asks for it). The
tool refuses any other field with a 400. The platform picks the batch size and the
modality itself, so do not send `batch_size` or `modalities`.

The reply's `message` carries the platform's quote: the cells it counts and the most it
will cost. Relay that figure; do not recompute it. Get an explicit yes, then call
`resolveConfirmation({ id: confirmation_id, decision: "approve" })`. If the user declines,
resolve it with `"reject"`.

Do not put several runs behind one approval, and do not treat an earlier "sounds good"
about the plan as approval for the run itself. Each model is its own run with its own
quote.

To change anything, reject the queued confirmation, then queue again. A 409 means the same
run is already queued. `listPendingConfirmations()` finds it, but its entry carries no
price, so if you do not still have that quote from this conversation, reject it and queue
again. For a 402 or an unusable quote, see `check-credits`.

## 4. Follow it through

On approval, `outcome.dag_run_id` names the run. Poll `getRunDetails({ runId })` until
`state` is `succeeded`, `failed` or `cancelled`. Runs take minutes to hours; poll at a
sensible interval and keep the user informed rather than going silent. On failure,
`getDagErrors({ runId })` says what went wrong. A failed or cancelled run is refunded in
full. `listDagRuns({ dagIds: "embedding" })` lists recent embedding runs if you lose track
of one.

## 5. Download the outputs

`getRunDetails({ runId })` returns `artifacts[]`, each with an `artifact_id`,
`artifact_type`, `display_name` and `s3_key`. The embedding matrix has `artifact_type`
`"embedding"`.

`downloadArtifact({ artifactId })` returns a presigned URL. Fetch it with
`curl -o '<fileName>' '<url>'`; it needs no auth header and expires within an hour. It
serves one file of any size, binary included, but not a directory.

If the matrix carries cell ids, join on them. If it is a bare matrix, check its row count
equals the dataset's `cellCount` before assuming its rows follow the dataset's order, and
say you assumed it.

A UMAP is its own run. If one consumed this embedding, it is listed in the run's
`child_runs`, and its `umap` artifact downloads the same way.

## Hand-off

- Want the model trained on labels first? → `fine-tune-model`. Its result must be promoted
  before it can be embedded here.
- Want to knock out, knock down or overexpress genes? → `in-silico-perturbation`: edit the
  counts locally, upload, and embed the perturbed file here.
- Cost and balance → `check-credits`.
- Judging whether an embedding is good (separation of the conditions of interest against a
  zero-shot baseline) is interpretation, not something these tools report.

## Conventions

`datasetId`, never a path · scope is derived from the signed-in user, you cannot pass a
project · send only the schema's fields · queue → relay the quote → explicit yes →
`resolveConfirmation` · one model, one run, one approval · poll the run · download with
`downloadArtifact` · never print credentials or raw upstream errors.
