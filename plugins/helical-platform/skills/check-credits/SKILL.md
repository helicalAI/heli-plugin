---
name: check-credits
description: >-
  Answer questions about Helical credits — balance, spending, buying more — and explain a
  run refused for insufficient credit. Use when the user asks what their balance is, what
  they have spent, how to top up, or why a run would not start.
---

# Check credits and top up

**You cannot read the balance.** Nothing on this surface returns a credit figure, so the
answer to "how many credits do I have?" is never a number you supply.

Send the user to **console.helical.bio** — the site they signed in to when they connected
this plugin. Credits, spending, receipts and account settings all live there, and
`console.helical.bio/docs` covers anything else they ask about the product.

It is **not** `helical.bio`. That is the marketing site and holds nothing about their
account; sending them there is the same wrong turn as inventing a menu. Beyond those two
addresses, describe the destination rather than guessing a deeper URL — a full link is
safe to pass on only when the platform itself gave it to you.

Do not describe a profile menu, an avatar dropdown, or a workspace billing screen. That is
a different Helical product, and inventing navigation sends a paying customer looking for
something that does not exist.

## When a run is refused for insufficient credit

The tool error already states the shortfall and links to where credit is bought. **Relay
it as it stands** — do not restate the numbers, do not derive a balance from them, do not
drop the link. Worth adding, because the message does not say it: nothing was charged, and
retrying unchanged will fail the same way until credit is added.

## Before committing to a run

`estimate_embedding_run` and `estimate_finetuning_run` are free and return the price. Give
the user that figure and let them compare it against their balance on the site — do not
guess whether it will clear.
