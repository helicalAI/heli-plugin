---
name: run-helical-locally
description: >-
  Preview the planned local Helical workflow without inspecting the machine, installing
  packages, or running computation. Use only when the user asks to inspect the pro forma
  local-execution preview; this preview has no MCP dependency.
---

# Run Helical locally

## Pro forma preview gate

The installed plugin is currently a pro forma preview. Stop here: do not follow the
workflow below, inspect the machine, install packages, or run local computation. This
skill intentionally has no MCP dependency, so explain that local execution is unavailable
in the preview. The remaining sections document intended future behavior only.

Uses the open-source package (`pip install helical`, AGPL-3.0) and the user's own compute,
through your shell and code-execution tools. **Nothing here touches the hosted platform**:
no account, no credit, no cost, and no run appears in their platform history.

## When this is the right choice

Recommend local when any of these hold:

- **The data must not leave their machine.** Unpublished results, patient-derived data, a
  collaboration agreement that forbids upload. This is the case where local is not merely
  cheaper but the only correct answer — say so plainly rather than offering the hosted path
  as an alternative.
- They already have a CUDA GPU and the dataset fits in its memory.
- They want to try several models on a subsample before committing to anything.
- They want a model the platform does not host: **Tahoe-X1, Caduceus, Evo 2, GenePT**.

Recommend the **hosted** tools instead when: there is no local GPU; the dataset is large
enough that the run should survive a closed laptop; the output needs to persist, be
versioned, or feed later platform work; or a fine-tuned model must be registered for reuse.

Say which you are recommending and why in one sentence. Do not silently pick.

## 1. Preflight — check before installing anything

A blind install on the wrong interpreter fails slowly and confusingly. Establish, in this
order, and stop with a clear statement if any check fails:

1. **Python version.** The package requires **≥ 3.12 and < 3.13**. Not 3.11, not 3.13.
   If the user's default interpreter is outside that range, say so and offer to create a
   3.12 environment rather than fighting their global install.
2. **GPU.** `nvidia-smi` for the device and driver. CPU-only is workable for very small
   inputs on some models, but say that it will be slow — and for **Caduceus it is a hard
   blocker**, because the `mamba_ssm` dependency is CUDA-only.
3. **Compute capability, if they want Evo 2.** It requires **≥ 8.9**. Below that, report
   the blocker and do not claim the model ran.
4. **Disk and time.** The install pulls `torch` and CUDA runtime libraries — several GB.
   Warn before starting, not halfway through.

## 2. Install

```
pip install helical
```

For a specific CUDA build, add the matching index, e.g.
`--extra-index-url https://download.pytorch.org/whl/cu130`. Extras when the model needs
them: `helical[mamba-ssm]` for Caduceus and Mamba2-mRNA, `helical[evo-2]` for Evo 2.
Flash-attention, if used, needs `pip install flash-attn --no-build-isolation`.

Prefer a virtual environment. Never install into a system interpreter without asking.

## 3. Run

The package follows one shape across models: construct the model with its config, call
`process_data` on an `AnnData` object or sequence list, then `get_embeddings`. Consult the
model card for the exact class and config names rather than guessing — the docs are at
`helical.readthedocs.io`, and the model cards are the authority on each model's inputs,
supported species, and limitations.

Write the script to a file and run it rather than piping a long heredoc, so the user keeps
something they can re-run and edit.

Report the same things the hosted path reports: which model and variant, the input cell or
sequence count, and the output shape reconciled against it.

## 4. Be honest about the limits

The same rules as the hosted skills, and for the same reasons:

- Do not claim batch correction from embeddings alone.
- Do not report accuracy without a held-out split the user actually supplied.
- Do not present a research output as a clinical decision.
- Do not relabel a sequence embedding as a structure prediction.
- Choose a model whose documented species coverage matches the data, and say which you
  chose.

## Cross-backend results are not comparable

An embedding computed locally and one computed on the platform can differ numerically: the
installed package version and the platform's pinned image are not the same build. If the
user wants to compare, compare within one backend. Presenting a local result against a
hosted result as a *model* comparison is wrong — it is a version experiment.

## Licence

The package is AGPL-3.0. The user installs it on their own machine and this plugin
redistributes nothing, but mention the licence if they ask what they are installing, or if
they are considering building a product on top of it.

## Conventions

Preflight before install · state which backend you recommend and why · local runs cost
nothing and are invisible to the platform · report shapes against the input · never
overstate what an embedding supports · do not compare results across backends.
