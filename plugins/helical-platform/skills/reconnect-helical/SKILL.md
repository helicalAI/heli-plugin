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
message.** No message about a broken Helical connection leaves without the destination in
it — not even one that cannot say why the connection broke. Leaving them to ask "so where
do I sign in?" is the one failure this skill exists to prevent.

## Say where to go, first time

> Your Helical sign-in needs renewing. Sign in again at <console-url-link-from-who-am-i-tool> and I'll
> pick the embedding back up — nothing has started, so nothing has been charged.

Adapt the four parts; do not reuse the sentence:

- **What broke**, in plain words. Do not say "expired" unless you established that it
  expired, and never quote the OAuth code at the user.
- **Where to go** — the account site. `check-credits` owns its address and pages, so
  consult that skill rather than reasoning about URLs here.
- **What you will pick back up** once they are in, named as the user would name it.
- **What it cost, only where true.** "Nothing was charged" holds when the failure stopped
  something before it started, and is false when a session dropped part-way through a run
  that is still going. Say which case it is, or say nothing.

**A sign-in link the failure carried beats the site** — a connect URL the platform pushed.
Relay that one instead, as it stands: it is specific to this account and this flow.

## When the tools are not there at all

No `helical` tools in the session — or the host calling the plugin enabled while reporting
authentication as "unknown" — is this same failure with no error to read. It is not a
reason to investigate the endpoint: a `405` to a plain `GET` proves only that the URL is
up, and says nothing about whether the user is signed in. Give the destination anyway, and
say that a new session is needed, because hosts load their tool surface at session start
and signing in now will not populate this one:

> None of the Helical tools loaded here, which means this connection is not signed in.
> Sign in at **console.helical.bio**, then start a new session and ask me for the health
> check again — reconnecting mid-session will not bring the tools back.

"Reconnect or reload the plugin" without an address is the failure this replaces: it names
a chore instead of a destination.

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
helical backend, and the client — not you — holds the credential for it, so the fix is
that client's command. On Claude Code the plugin registers the server as
`plugin:helical-platform:helical`, so it is `claude mcp login plugin:helical-platform:helical`,
or `claude mcp logout plugin:helical-platform:helical && claude mcp login
plugin:helical-platform:helical` when it is the stored registration being rejected
(`invalid_request`, `invalid_client`, "not registered"); a server the user added by hand
with `claude mcp add` is plain `helical` — `claude mcp list` shows which name this session
has. On Codex, `codex mcp login helical`. On any other
host, name that client's own reconnect affordance or say you do not know it — do not invent
menu items. Run the command yourself if you can: it opens their browser and completes on
its own callback, so their whole job is approving the page. With no browser on that machine,
`--no-browser` prints a URL to open elsewhere and paste back.

**Never build a sign-in URL.** Assembling one, or registering a client with `curl` to get
something clickable, authorizes a client you created and leaves the credential the plugin
uses untouched: the user signs in, it works, and the next call fails identically. Relay
only the account site, or a link the platform or the host printed itself.

## Then

`health()` and `whoami()` are the cheapest way to confirm the connection is back: both take
no arguments, spend no credit and start nothing. Prefer `whoami` when the user was
mid-workflow, since it also returns the current balance.

Retry the failed call once, and stop rather than loop if it fails on authentication again.

**Before re-issuing anything that starts work**, check what the dropped session left
behind — starting over is how one job gets billed twice:

- `listPendingConfirmations()` for a run queued but not yet approved. A `triggerEmbedding`
  that landed before the failure has already queued a confirmation, and re-triggering
  returns a 409 without giving the id back — so list it and resolve that one instead.
- `listDagRuns({ state: "running" })` for a run already going, and
  `getConfirmationStatus({ id })` to settle whether an approval that timed out actually
  launched.

That second case is also where "nothing was charged" stops being true: an approval spent
before the connection dropped has already launched the run. Check before saying it.

## Conventions

Where to go, in the first message, always · absent tools are the same failure as a
rejected one · a new session after signing in, where the host loads tools at start · the
account site unless the platform sent its own link ·
plain words, never the OAuth code · "nothing was charged" only when true · the client
command second, never first · never build a sign-in URL · retry once, then stop · check
for a queued confirmation or a running run before re-issuing anything that spends credit.
