---
name: fine-tune-model
description: >-
  Preview the planned Helical fine-tuning workflow and tool contract. No training is
  implemented: at most one explicitly requested preview tool call returns Not implemented
  yet. Use only when the user asks to inspect or exercise the pro forma preview.
---

# Fine-tune a model

## Pro forma preview gate

The installed plugin is currently a pro forma preview. Stop here: do not follow the
workflow below, request credentials, or attempt any training. If the user explicitly asks
to exercise a fine-tuning MCP tool, make at most one matching call and relay its
`Not implemented yet` result. Otherwise explain that execution is unavailable. The
remaining sections document intended future behavior only.

Trains a foundation model on a labelled dataset and registers the result so it can be used
for embeddings.

`startFinetuningRun` starts real compute immediately, and it is the most expensive thing
on this surface. Nothing downstream will ask the user to confirm, so **you are the
confirmation step** — and the preparation below matters more than it does for embeddings,
because a misconfigured fine-tuning run wastes far more.

## 1. Select the dataset and confirm the label column

`listDatasets(...)` → `getDataset({ id })`. The record already names likely label
columns: `celltypeColumn`, `celltypeColumnLv2`, `diseaseColumn`, `donorColumn`.

Then verify rather than assume:

- `getDatasetColumns({ id })` → the actual `.obs` column names.
- `getDatasetObsValues({ id, column })` → that column's distinct values.

**Confirm the intended column with the user before starting.** Training on a plausible-
looking but wrong column wastes the whole run, and nothing downstream will catch it. Check
the classes look like what they expect — a column with one dominant class or hundreds of
singletons will not train usefully.

## 2. Choose a base model

`listModels({ modelType?, status? })`. Fine-tune from a base model, or
from an already fine-tuned one to train further.

Before committing: if the user's goal is representations rather than a classifier, a
zero-shot embedding may already answer it. `compute-embeddings` is far cheaper and faster.
Say so rather than fine-tuning by default.

## 3. Confirm, then run

The API has **no defaults** for most training parameters — every one of these is required
and a missing one is a 400:

```
startFinetuningRun({
  datasetId, model,
  labels:      ["cell_type"],      # parallel arrays: one entry each per task
  task_type:   ["prediction"],     # "prediction" | "contrastive"
  model_type:  ["classification"], # "classification" | "regression"
  learning_rate, batch_size, epochs, logging_steps,
  lr_scheduler,                    # "constant" | "linear" | "cosine_with_min_lr"
  min_lr, val_steps, seed, num_trainable_layers,
  registered_model_name,           # letters, digits, _ - { } only
  # optional: model_version, valDatasetId, val_split, weight_decay,
  #           early_stopping_patience, device
})
```

`labels`, `task_type` and `model_type` must be the same length — one entry per task.

Reasonable starting values when the user has no preference: `learning_rate` 1e-4,
`batch_size` 16, `epochs` 1, `logging_steps` 10, `lr_scheduler` `"constant"`, `min_lr`
1e-6, `val_steps` 500, `seed` 42, `num_trainable_layers` 2. State the values you chose —
they are your decisions, not platform defaults, and the user may want different ones.

Start with a single epoch. It is cheaper to run again than to discover after a long run
that the configuration was wrong.

`registered_model_name` must not be only a model prefix (`scGPT_` alone is rejected). Give
it a name that says what makes this model different.

Call `startFinetuningRun` with exactly the arguments you intend to run. **It starts
nothing and costs nothing** — it prices the request and returns a pending confirmation
carrying the quote. **Because epochs multiply the token count, a fine-tuning quote is
usually many times an embedding quote on the same dataset** — show the user the number and
the epoch count it assumes.

**Get their explicit yes, then `resolveConfirmation({ id, decision: "approve" })`**,
restating the dataset, the label column, the base model, the epoch count, and the price
before you do. That approval is the billable call. This is the expensive operation; an
approval for an embedding run is not an approval for this. Change the epochs and you need
a new request — the confirmation prices the arguments it was created with.

## 4. Follow it through

`listDagRuns({ dagIds: ["finetuning"] })` then `getRunDetails({ runId })` until terminal.
Fine-tuning runs are long; poll at a sensible interval and keep the user informed.

## 5. Use the result

On success the model is registered and appears in `listModels` — with
`status: "unpromoted"` until someone promotes it. Its `name` is what you pass as `model`
to `compute-embeddings`.

## Judging whether it worked

Be honest about the limit: these tools report run state and artifacts, not a training
curve or a metric. `getRunDetails` gives artifacts, and `readFile` can read a text
metrics file if the run wrote one — but binary checkpoints cannot be inspected here.

The practical comparison available on this surface is an embedding from the base model
against one from the fine-tuned model, judged on the UMAP. Offer that rather than claiming
a quality verdict the tools cannot support.

## Conventions

`datasetId`, never a path · scope is derived from the signed-in user · verify the label
column before starting · every training parameter is required, there are no server-side
defaults · estimate → show the price → explicit yes → run with the quote · poll the run · never print
credentials or raw upstream errors.
