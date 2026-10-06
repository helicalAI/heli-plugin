---
name: reconnect-helical
description: >-
  Get a Helical connection working when it is not: the plugin was installed but never
  connected, a tool call came back with an authorization link, or a connection that worked
  before is now refused. Use when the helical tools are missing from the session or the
  host offers only an `authenticate` tool for it; when a Helical call returns "Authorization
  required before this tool can run", "This request requires more information" or a link to
  open; when it fails with invalid_grant, invalid_request, invalid_client, invalid_token,
  unauthorized_client, a 401, "OAuth authorization required", "Missing Bearer token" or
  "Invalid JWT"; when a call fails without saying why; or when the user asks how to sign in.
  For a run refused over credit, use check-credits.
---

# Reconnect Helical

Helical asks for two approvals, both in the browser: **connecting the plugin** in the
client, and **one authorization the first time a tool runs**. Each failure below is one of
them missing. Tell which from what you see, and put the user's next step in the same
message that reports the problem — never leave them to ask "so what do I do?".

| What you see | Case |
|---|---|
| No `helical` tools at all, or only an `authenticate` tool for it | 1 — not connected |
| A call returns a link to open, "Authorization required before this tool can run", or "This request requires more information" | 2 — authorization link |
| A connection that worked before now fails with a 401 or an OAuth code | 3 — connection refused |

Not these: **`402` / `insufficient_balance`** is credit (`check-credits`), and **`403`** is
a signed-in account without permission — reconnecting returns the same `403`.

## 1. Not connected

Installing the plugin and connecting it are two separate steps in every client. Skip the
second and the plugin shows as enabled with none of its tools. Confirm it before you fix
it, where the client lets you:

- **Claude Code**: `claude mcp list`. The plugin's server is
  `plugin:helical-platform:helical` (`claude.ai Helical` when added from the Claude
  directory, plain `helical` when added by hand). `! Needs authentication` is this case;
  `✔ Connected` is not.
- **Codex**, and the ChatGPT desktop app with a plugin added from GitHub: `codex mcp list`.
  `Not logged in` under **Auth** is this case; `OAuth` is not.
- **ChatGPT** from the public directory, **Claude Desktop** and **claude.ai** have no
  command: ask whether Helical still offers **Connect** where the fix below points.

Then connect it:

- **Claude Code** (CLI, and the Code tab in Claude Desktop). If the host offers a helical
  `authenticate` tool (e.g. `mcp__plugin_helical-platform_helical__authenticate`), call it
  and relay the link it returns; the tools load once the user approves, with no new
  session. If their browser then lands on a `localhost` page that will not load, ask for
  the address-bar URL and pass it to the matching `complete_authentication` tool.
  Otherwise the user runs `/mcp` → the Helical server → **Authenticate**, or
  `claude mcp login plugin:helical-platform:helical` in their own terminal. Do not run that
  command yourself: it needs an interactive terminal and refuses without one.
- **Codex**: `codex mcp login helical`. You can run this one: it opens their browser and
  waits for the approval. `/mcp` inside Codex only lists servers. In the ChatGPT desktop
  app the same step is **Settings** → **MCP servers** → **Authenticate**.
- **ChatGPT** from the public directory: **Settings** → **Plugins** → Helical Platform →
  **Connect**.
- **Claude Desktop** and **claude.ai**: **Customize** → **Connectors** → Helical →
  **Connect**.

Except through the `authenticate` tool, say a new session may be needed: hosts that load
their tools at session start will not add them to this one. The Connect step of the
walkthrough at `<who-am-i-tool-console-url>docs?section=walkthrough` has the same steps
for every client.

## 2. Authorization link

The connection is fine; Helical needs one more approval before the tool can run. It is
expected on the first tool call after connecting, and again now and then — months apart,
or after the account was signed out everywhere. Reconnecting the client does not clear
it, and neither does anything in case 1 or 3.

The link reaches you in one of two shapes: the host shows its own prompt to open a URL, or
the call fails with "Authorization required before this tool can run" followed by the
link. Relay that link exactly as it came — it belongs to this user and this request. 
The user opens it and approves (normally one click, since they are already signed in),
then you retry the same call.

## 3. Connection refused

A connection that worked before is rejected: a `401`, "OAuth authorization required",
"Missing Bearer token", "Invalid JWT", or an OAuth code (`invalid_grant`,
`invalid_request`, `invalid_client`, `invalid_token`, `unauthorized_client`). These come
from the client's sign-in, not from the tool you called, so its arguments are never the
problem. Never quote the code at the user; say what broke in plain words, and do not say
"expired" unless you know it expired.

The client holds that sign-in, so the fix is the client's — clear it and connect again:

- **Claude Code**: `claude mcp logout plugin:helical-platform:helical && claude mcp login
  plugin:helical-platform:helical`, in the user's own terminal, or `/mcp` → the Helical
  server → **Re-authenticate**.
- **Codex**: `codex mcp logout helical && codex mcp login helical`.
- **ChatGPT**: **Settings** → **Plugins** → Helical Platform → **Reconnect** (or
  **Disconnect**, then **Connect**).
- **Claude Desktop** and **claude.ai**: **Customize** → **Connectors** → Helical →
  **Connect**.

On any other host, name that client's own reconnect affordance or say you do not know it —
do not invent menu items.

**When the error does not say** which case it is, do not end the turn on "I cannot tell".
Check the status commands in case 1, then give the step that matches.

## Never build a link

Assembling a sign-in or authorization URL, or registering a client with `curl` to get
something clickable, authorizes something the plugin does not use: the user approves it,
it seems to work, and the next call fails the same way. Relay only a link the host or a
Helical tool printed.

## Then

`health()` and `whoami()` confirm the connection is back: both take no arguments, spend no
credit and start nothing. Prefer `whoami` when the user was mid-workflow, since it also
returns the current balance.

Retry the failed call once, and stop rather than loop if it fails the same way again.

**Before re-issuing anything that starts work**, check what the failed attempt left
behind — starting over is how one job gets billed twice:

- `listPendingConfirmations()` for a run queued but not yet approved. A `triggerEmbedding`
  that landed before the failure has already queued a confirmation, and re-triggering
  returns a 409 without giving the id back — so list it and resolve that one instead.
- `listDagRuns({ state: "running" })` for a run already going, and
  `getConfirmationStatus({ id })` to settle whether an approval that timed out actually
  launched.

"Nothing was charged" holds only when the failure stopped something before it started. An
approval spent before the connection dropped has already launched the run, so check before
saying it.

## Conventions

The next step in the first message, always · tell the three cases apart before fixing ·
check status with `claude mcp list` / `codex mcp list` where the client has one · call the
host's `authenticate` tool when it offers one · run `codex mcp login` yourself, hand
`claude mcp login` to the user · relay an authorization link exactly as it came, then
retry · plain words, never the OAuth code · never build a link · retry once, then stop ·
check for a queued confirmation or a running run before re-issuing anything that spends
credit.
