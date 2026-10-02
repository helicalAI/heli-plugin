---
name: check-credits
description: >-
  Read the user's Helical credit balance, tell them what a run will cost and when they are
  actually charged, and explain a run refused for insufficient credit. Use when the user
  asks what their balance is, whether they can afford a run, what an embedding or
  fine-tune will cost, or why a run would not start.
---

# Credits: read the balance, explain the charge

## The balance is readable

`whoami()` returns the signed-in `email`, the current `credits_balance`, and the `console`
URL. When the user asks how many credits they have, that number is the answer — give it.

**One credit is one USD**, so a credit figure is a dollar figure and there is nothing to
convert.

The figure is current as of the call, and it moves as runs are charged and refunded. Read
it again rather than reusing one from earlier in the conversation.

## What a run costs

A run costs **a fixed amount per job plus a rate per processed cell**, not per token:

```
credits = jobs × (credits_per_job + cells × passes × credits_per_cell)
```

- `jobs` is how many jobs the launch runs, and `cells` is the cells in each. An embedding
  launch is **one job**.
- `credits_per_job` pays for the work a job does before its first cell, so it is the same
  for ten cells or a million. On a small dataset it is most of the cost.
- `passes` is **1 for an embedding** and **the epoch count for fine-tuning** — which is why
  fine-tuning the same dataset costs several times what embedding it does. The per-job
  amount is paid once, not once per epoch.
- Both rates are on every `listModels` entry, and differ per model. A model whose rates are
  `null` is not priced and cannot be run here; it is not free.
- A fine-tuned model is charged at the rates of the `base_model` it descends from; it has no
  rates of its own and could not have them, since the user creates it at run time.
- There is no deduplication — the same cell twice is two processed cells.

Use this when the user is deciding **whether** to run: `cellCount` from `listDatasets` or
`getDataset`, and the two rates from `listModels`. For example, with a model at
`credits_per_job` 0.03 and `credits_per_cell` 0.000003, embedding 300,000 cells is
`1 × (0.03 + 300000 × 1 × 0.000003)` = **0.93 credits**. Read the real rates from `listModels`
rather than reusing these.

**But your arithmetic is an estimate, not the quote.** When a run is actually queued,
`triggerEmbedding` prices it and returns the platform's own figure which is what the charge will 
read. Queuing is free and starts nothing, so prefer that figure once it exists, and relay it rather
than recomputing.

## When the charge actually happens

This is usually the user's real question, and it is reassuring:

- Queuing a run charges nothing.
- The credit is taken **when the run starts**, at approval.
- A run that **fails or is cancelled is refunded in full**.

So a failed run costs nothing. Say so plainly when one fails — it is the first thing they
will want to know.

## When the balance will not cover it

A **402 `insufficient_balance`** can come back either when the run is queued or when it is
approved. Either way nothing launched and nothing was charged — but **the confirmation is spent**.
A later change to the balance does not make it approvable again; the run has to be re-requested
from the trigger tool, which mints a fresh quote.

Credits are managed in their Helical account, not through this plugin.

Retrying the same request unchanged will not help, and the error says so. Do not loop.

## Where the account lives

The account lives on the console, **console.helical.bio**: the site the user signed in to when
they connected this plugin. Use the `console` URL `whoami` returned. Account settings, usage
history and docs live there.

It is **not** `helical.bio`. That is the marketing site and holds nothing about their
account, so sending them there is the same wrong turn as inventing a menu. Beyond the pages
named above, describe the destination rather than guessing a deeper URL.

Do not describe a profile menu, an avatar dropdown, or a workspace billing screen. That is
a different Helical product, and inventing navigation sends a paying customer looking for
something that does not exist.


## Conventions

Read the balance with `whoami`, never guess it · one credit is one USD · re-read rather
than reuse · a fixed amount per job plus a rate per processed cell, no deduplication · your arithmetic estimates, the trigger
quotes — prefer the quote · charged at start, refunded in full on failure or cancellation ·
a 402 spends the confirmation, so re-request the run once the balance covers it ·
the console URL the platform gave you, never `helical.bio`, never an invented menu.
