---
name: fine-tune-model
description: >-
  Fine-tune a Helical foundation model on a labelled single-cell dataset. Use when the
  user wants to personalize, train, or adapt a model to their own labels. For
  representations from an existing model without training, use compute-embeddings
  instead.
---

# Fine-tune a model

Trains a foundation model on a labelled dataset and registers the result in the user's
model list.

`triggerFinetuning` does not start anything. It queues the run and returns a
`confirmation_id` and the platform's price. The run starts only when
`resolveConfirmation` approves it, and **that call is the user's approval**. This is the
most expensive run on this surface, so the preparation below matters more than it does for
embeddings.

**Every run that spends credit needs the user's explicit approval.** That approval comes at
`resolveConfirmation`, after the platform's quote. So do not ask the user to approve the
configuration before queuing. Make the choices below, queue the run, then ask once, showing
the choices and the price together. Queuing is free. To change a choice, reject the queued
run and queue again.

## 1. Select the dataset and choose the label column

`listDatasets(...)` → `getDataset({ id })`. The record names likely label columns:
`celltypeColumn`, `celltypeColumnLv2`, `diseaseColumn`, `donorColumn`. Note `cellCount`
too; the price scales with it.

These tools cannot list a dataset's other `.obs` columns or their values. If the user wants
a column the record does not name, ask them for its exact name, or read it from a local
copy of the file (the record's `linkData`, when it has one; an uploaded dataset usually
does not).

**Choose the label column, and name it in the approval request.** Training on a
plausible-looking but wrong column wastes the whole run, and nothing downstream will catch
it. Where you can see the classes, check they look like what the user expects. A column
with one dominant class or hundreds of singletons will not train usefully. Ask before
queuing only when you cannot choose: several columns fit the request, none does, or the
classes look unusable.

**Validation needs its own dataset.** This surface does not split the training data. To
validate, the user registers a separate held-out dataset and you pass it as `valDatasetId`.
Without one, the run trains with no validation.

## 2. Choose a base model

`listModels()`. Fine-tune from a `base_models` entry: pass its `name` as `model` and omit
`model_version`. To train an existing fine-tune further, pass a `models` entry's `name`
plus its `version`, as a number, in `model_version`, promoted or not. Pick a model whose
`credits_per_cell` is not `null`; an unpriced model cannot run here.

Before committing: if the user wants representations rather than a classifier, a
zero-shot embedding may already answer it. `compute-embeddings` is far cheaper and faster.
Say so rather than fine-tuning by default.

## 3. Queue it

These fields are required, and a missing one is a 400:

```
triggerFinetuning({
  datasetId, model,
  labels:      ["cell_type"],      # one entry per prediction task
  task_type:   ["prediction"],     # "prediction" | "contrastive"
  model_type:  ["classification"], # one per prediction task: "classification" | "regression"
  learning_rate, batch_size, epochs, logging_steps,
  lr_scheduler,                    # "constant" | "linear" | "cosine_with_min_lr"
  min_lr, seed, num_trainable_layers,
  registered_model_name,           # letters, digits, _ - { } only
  # optional: model_version, valDatasetId, weight_decay, warmup_steps,
  #           early_stopping_patience, lora_config, task_weights, ...
})
```

`labels` and `model_type` describe the **prediction tasks only**, one entry each per task,
in the same order. `task_type` lists one entry per task, contrastive included. The tool's
own description calls all three parallel; that holds only when every task is a prediction.

At most one task can be contrastive. It takes no `labels` entry; its columns go in its own
fields, and nothing checks them before the run starts:

- `loss_type: "supcon"` needs `supcon_label_columns`, `temperature` and
  `n_labels_per_batch` (at most `batch_size`).
- `loss_type: "pairwise"` needs `pairwise_similarity_columns` and `margin`.

Two optional fields carry conditions the server does not check either.
`early_stopping_patience` needs a `valDatasetId`, and `warmup_steps` needs a non-constant
`lr_scheduler`. A bad combination is charged and then fails, so get these right before
queuing.

**Send only fields the schema lists.** It refuses an unknown field with a 400. Three
common ones are not offered here: `val_split` (pass `valDatasetId` instead),
`val_steps` (validation runs once an epoch) and `hyperparameter_config` (one run trains
one configuration).

Reasonable starting values when the user has no preference: `learning_rate` 1e-4,
`batch_size` 16, `epochs` 1, `logging_steps` 10, `lr_scheduler` `"constant"`, `min_lr`
1e-6, `seed` 42, `num_trainable_layers` 2. Queue with them; do not stop to ask first. They
are your decisions, not platform defaults, so list them in the approval request.

Start with a single epoch. It is cheaper to run again than to find out after a long run
that the configuration was wrong.

`registered_model_name` must not be only a model prefix (`scGPT_` alone is rejected). Give
it a name that says what makes this model different.

## 4. Show the price, then approve

The reply carries the platform's quote: the cells it counts and the most it
will cost. Relay that figure; do not recompute it. It is usually many times an embedding
of the same dataset, because each epoch makes several passes over the training cells, and
validation adds a pass over the validation cells each epoch.

**Ask for the user's explicit yes in one message.** Name the dataset, the label column, the
base model, the values you chose and the quoted price. Say the run is queued and waits for
their yes, and that any value can be changed before it starts, with a new quote. An
approval for an embedding run is not an approval for this one, and an earlier "sounds good"
about the plan is not approval for the run. Then call `resolveConfirmation({ id: confirmation_id, decision: "approve" })`.
If they decline, resolve it with `"reject"`.

To change anything, reject the queued confirmation, then queue again with
`triggerFinetuning`; that mints a new quote. A confirmation left pending stays approvable
until it expires.

A 409 from `triggerFinetuning` means the same run is already queued.
`listPendingConfirmations()` finds it, but its entry carries no price and does not show
the epochs or learning rate. If you do not still have that confirmation's quote from this
conversation, reject it and queue again, so the user approves a price they have seen.

For a 402 or an unusable quote, see `check-credits`.

## 5. Follow it through

On approval, `outcome.dag_run_id` names the run. Poll `getRunDetails({ runId })` until
`state` is `succeeded`, `failed` or `cancelled`. Fine-tuning runs are long, so poll at a
sensible interval and keep the user informed. On failure, `getDagErrors({ runId })` says
what went wrong. A failed or cancelled run is refunded in full.

On success the model is registered under `registered_model_name` and shows in
`listModels` `models`, unpromoted. Promotion is only a label here: the model can be used
straight away for embeddings or further training. Pass its `name` and `version`. It is
charged at its `base_model`'s rates.

## Judging whether it worked

Be honest about the limit: these tools report run state and artifacts, not a training
curve or a metric. `getRunDetails` lists the artifacts. `downloadArtifact` fetches a
single file, such as a metrics file if the run wrote one, but not a checkpoint directory.

The practical comparison here is an embedding from the base model against one from the
fine-tuned model, judged on the UMAP. Offer that rather than
claiming a quality verdict the tools cannot support.

## Conventions

`datasetId`, never a path · scope is derived from the signed-in user · check the label
column before queuing · validate with a separate dataset, never a split · send only the
schema's fields · no approval before queuing · queue → choices and quote in one message →
explicit yes → `resolveConfirmation` · poll the
run · a new fine-tune can run here at once, promoted or not · never print credentials or
raw upstream errors.
