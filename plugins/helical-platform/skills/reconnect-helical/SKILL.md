---
name: reconnect-helical
description: >-
  Tell the user where to go to reconnect when a Helical call fails on authentication, in
  the message that reports the failure. Use when a Helical tool call fails with
  invalid_grant, invalid_request, invalid_client, invalid_token, unauthorized_client, a
  401, "OAuth authorization required", "Missing Bearer token", "Invalid JWT" or a host's
  paraphrase of one of those ("This request requires more information"); when a Helical
  call fails in a way that does not say whether authentication is the cause; when the
  helical tools are missing from the session; or when the user asks where to sign in. For
  a run refused over credit, use check-credits.
---

# Reconnect Helical

When a Helical call fails on authentication, **the user's next click belongs in that same
message.** Leaving them to ask "so where do I sign in?" is the one failure this skill
exists to prevent.

## Say where to go, first time

> Your Helical sign-in needs renewing. Sign in again at **console.helical.bio** and I'll
> pick the embedding estimate back up — nothing has started, so nothing has been charged.

Adapt the four parts; do not reuse the sentence:

- **What broke**, in plain words. Do not say "expired" unless you established that it
  expired, and never quote the OAuth code at the user.
- **Where to go** — the account site. `check-credits` owns its address and pages, so
  consult that skill rather than reasoning about URLs here.
- **What you will pick back up** once they are in, named as the user would name it.
- **What it cost, only where true.** "Nothing was charged" holds when the failure stopped
  something before it started, and is false when a session dropped part-way through a run
  that is still going. Say which case it is, or say nothing.

**A link the failure carried beats the site** — a connect URL the platform pushed, the
top-up link in a credit refusal. Relay that one instead, as it stands: it is specific to
this account and this flow.

## Which failures these are

Any OAuth code, by definition: `invalid_grant`, `invalid_request`, `invalid_client`,
`invalid_token`, `unauthorized_client`. They come from the sign-in path, not from the tool
you called, so its arguments are never the problem. Hosts paraphrase the code and discard
the description that explained it — **"This request requires more information" is
`invalid_request`** — so treat the paraphrase as the code. Also a `401`, "OAuth
authorization required", "Missing Bearer token", "Invalid JWT", or the `helical` tools
missing from the session entirely.

Not these two: **`402` / `insufficient_balance`** is credit rather than sign-in
(`check-credits`), and **`403`** is an authenticated account without permission —
reconnecting returns the same `403`.

**When the error does not say**, do not end the turn on "I cannot tell whether this is
authentication". Signing in is free and rules out the whole path at once, so say what you
do and do not know, and give the link anyway.

## If signing in on the site does not clear it

Then the connection's own stored credential is what is being refused. The plugin talks to
`api.helical.bio`, and the client — not you — holds the credential for it, so the fix is
that client's command: `claude mcp login helical`, or `claude mcp logout helical && claude
mcp login helical` when it is the stored registration being rejected (`invalid_request`,
`invalid_client`, "not registered"). On Codex, `codex mcp login helical`. On any other
host, name that client's own reconnect affordance or say you do not know it — do not invent
menu items. Run the command yourself if you can: it opens their browser and completes on
its own callback, so their whole job is approving the page. With no browser on that machine,
`--no-browser` prints a URL to open elsewhere and paste back.

**Never build a sign-in URL.** Assembling one, or registering a client with `curl` to get
something clickable, authorizes a client you created and leaves the credential the plugin
uses untouched: the user signs in, it works, and the next call fails identically. Relay
only the account site, or a link the platform or the host printed itself.

## Then

Retry the failed call once, and stop rather than loop if it fails on authentication again.
If what failed was `start_embedding_run` or `start_finetuning_run`, check `list_runs`
before re-issuing it — a session that dropped mid-workflow can leave a run going, and
starting over is how one job gets billed twice. Re-price and re-confirm a stale quote
rather than reusing it.

## Conventions

Where to go, in the first message · the account site unless the platform sent its own link ·
plain words, never the OAuth code · "nothing was charged" only when true · the client
command second, never first · never build a sign-in URL · retry once, then stop.
