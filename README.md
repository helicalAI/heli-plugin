# Helical Platform plugin

The distributable plugin is currently an **internal pro forma preview**. It exposes the
planned skills and sixteen-tool MCP catalogue so installation, discovery, prompts, and
error presentation can be tested. Every declared tool call returns `Not implemented yet`
before tool-schema or business validation and before networking; it needs no credentials
and performs no action. The
remaining documentation describes the production design that will replace this preview.

The consolidated architecture, identity, metering, security, and artifact blueprint is in
[`DESIGN.md`](DESIGN.md); the phased delivery plan is in [`PLAN.md`](PLAN.md).

## Layout

```text
plugins/helical-platform/
├── .codex-plugin/plugin.json   # plugin manifest
├── .mcp.json                   # launches the STDIO server in default preview mode
├── mcp/server.py               # preview server + future API adapter reference
├── skills/
│   ├── compute-embeddings/     # hosted: dataset → model → estimate → run → outputs
│   ├── fine-tune-model/        # hosted: labelled dataset → trained, registered model
│   └── run-helical-locally/    # local: the open-source package on the user's own GPU
└── tests/                     # test_server.py + test_plugin_manifest.py
```

## Tool surface

Sixteen tools. The preview advertises their intended schemas but does not execute them.
Tool shapes follow the dashboard's `agentcore-mcp` routes — paths, parameter names and
response fields are taken from them — but the future target is the B2C port of those routes.

| Group | Tools |
|---|---|
| Models | `list_models` |
| Datasets | `list_datasets`, `get_dataset`, `get_dataset_columns`, `get_dataset_obs_values` |
| Estimates (free) | `estimate_embedding_run`, `estimate_finetuning_run` |
| Runs (**billable, start immediately**) | `start_embedding_run`, `start_finetuning_run` |
| Runs | `list_runs`, `get_run_details` |
| Outputs | `list_files`, `read_file`, `list_s3_files`, `list_umaps`, `get_umap` |

### Two surfaces, one client

B2C users never open the platform UI — only the four account screens: sign-up, sign-in,
top-up, and balance (DESIGN.md §2.5).
The MCP routes still run on the dashboard's APIs and database; what the B2C port
(`platform-mcp`, DESIGN.md §2.3) changes is the UI-shaped assumptions in them.

|  | `agentcore-mcp` (today) | `platform-mcp` (the port) |
|---|---|---|
| Project scoping | `conversationId` / `projectId` in the URL | derived from the subject; never transmitted |
| Triggers | queue for in-chat approval → `pending_approval` + `confirmation_id` | execute directly; confirmation is prompt-level in the client |
| Estimates, balance, upload, download | absent | added |

**Scope is derived, never transmitted** (DESIGN.md §2.3.1). It is in no input schema, no
path, no query, no header, no body — the API resolves the caller's project from the
verified subject. A transmitted identifier is an input to be validated; a derived one is
not an input at all, so the class of bug where a caller names someone else's project does
not exist. Tests assert that no schema exposes scope and that no request carries it.

**The cost:** the routes that still want scope in the URL — `list_models` and both
triggers — will fail against `agentcore-mcp` until the port lands. Datasets, run details,
files, S3 and UMAPs scope from the resource or the caller's memberships and work against
either. `HELICAL_API_ROOT` selects the route group (default `/api/platform-mcp`).

There is also no `get_confirmation_status`: the approval queue is conversation-bound, and
with direct execution there is nothing to poll.

The approval queue matters for more than convenience: it renders in a dashboard chat a B2C
user never opens, so a run queued for them would never start. That is why DESIGN.md §6.1
moves confirmation into the client the user is actually in.

### Estimate before spend

One estimate endpoint **per operation**, because the token formula differs — rows × width ×
coefficient for embedding, × epochs for fine-tuning. Estimates are free, read-only, and
return a `quote_id` that the matching `start_*` tool **requires**, so a run cannot be
started without having been priced first. Changing any argument invalidates the quote.
That makes DESIGN.md §6.1's estimate-before-spend rule structural rather than advisory: a
test asserts an estimate's body is byte-identical to the run it prices, minus the quote.

### Not available on `agentcore-mcp` yet

No balance or billing route; no dataset upload, registration, or presigned
URL; no artifact download — outputs are reachable only as UTF-8 text under
`/projects/<project>/data`, capped at 1 MiB, so a binary `.npy` matrix can be located but
not read. The metered workflow in DESIGN.md §5 and §7 needs all of these, and they arrive
with the port. The scaffold does not pretend they exist.

## Two backends

The plugin offers the same intent through two execution paths, and the skills say which
they recommend rather than choosing silently.

**Hosted** (`compute-embeddings`, `fine-tune-model`) — the MCP tools above. Metered per
token, persisted, versioned, no local hardware needed.

**Local** (`run-helical-locally`) — the open-source package (`pip install helical`,
AGPL-3.0) on the user's own GPU. **This is a skill, not a tool**: our MCP server cannot
execute anything on the user's machine, so local mode drives the agent host's own shell and
code execution. It declares no MCP dependency. No account, no credit, no cost, and nothing
appears in the platform's history.

Local is the right answer — not merely the cheaper one — when **the data cannot leave the
user's machine**, and it is the only way to reach four models the platform does not host:
Tahoe-X1, Caduceus, Evo 2, GenePT. Hosted wins when there is no GPU, when the run should
outlive the session, or when the output must be registered for later platform work.

The package pins **Python ≥ 3.12 < 3.13** and a CUDA 13 torch build, so the skill checks
the interpreter, the GPU, and (for Evo 2) compute capability ≥ 8.9 *before* installing
several gigabytes. See DESIGN.md §7.2.

## Surface boundary

The checked-in `mcp/server.py` is the **local-development scaffold** and the reference for
the transport-safety controls any adapter must keep: HTTPS-only upstream, redirects
disabled so a redirect cannot forward the bearer token, bounded arguments, a 2 MB response
cap, a 30-second timeout, and errors surfaced as the API's short `error` string with its
`detail` field dropped, since that can carry Zod issues or exception text. In production the MCP endpoint is served by the
**dashboard itself** — a Streamable HTTP route in the same group as the tool routes.

The checked-in `.mcp.json` launches the scaffold in its safe default preview mode. MCP
initialization, ping, and tool discovery work normally; declared tool calls short-circuit
to a stable error without reading credentials or calling an API. The unfinished reference
adapter requires the explicit development-only `--reference-adapter` flag.

**Authentication follows the MCP OAuth proxy already provisioned per tenant**
(`infra/modules/tenant/user_pool_client_mcp.tf`, enabled on stage), which is the source of
truth: it presents the dynamic-registration surface MCP clients expect and Cognito does not,
holds the one confidential client secret, and proxies Authorization Code + PKCE to the
Cognito hosted UI. The dashboard verifies the resulting ordinary Cognito access token.

Note the name collision: the `helicalAI/helical-mcp` **repo** is a superseded proof of
concept, but "helical-mcp" in `infra` is that live proxy. See DESIGN.md §2, §3 and §4.

## Local development

The shipped preview needs `uv` and a preinstalled system Python (3.10 or newer); it runs
offline, disables managed-Python downloads, reads no credentials, and performs no
configured API call. To exercise the unfinished reference adapter directly, pass
`--reference-adapter` and keep secrets out of the repo:

```sh
export HELICAL_API_BASE_URL="https://platformdev.helical-ai.bio"   # dashboard origin
export HELICAL_API_TOKEN="a-cognito-bearer-token"
# export HELICAL_API_ROOT="/api/agentcore-mcp"   # the older, scope-in-URL surface
uv run --no-project python plugins/helical-platform/mcp/server.py --reference-adapter
```

## Validate

```sh
uv run python -m unittest discover -s plugins/helical-platform/tests -v
uv run --with pyyaml python /path/to/plugin-creator/scripts/validate_plugin.py plugins/helical-platform
uv run python /path/to/skill-creator/scripts/quick_validate.py plugins/helical-platform/skills/compute-embeddings
uv run python /path/to/skill-creator/scripts/quick_validate.py plugins/helical-platform/skills/fine-tune-model
```

Publisher, support, repository and licence metadata are real — the repo is **proprietary,
all rights reserved** ([LICENSE](LICENSE)). The preview declares no capabilities because it
performs no actions. Privacy and terms URLs, production capability labels, and an end-user
licence grant remain open before a functional or public distribution.

## Add to a repo marketplace

The repository includes `.agents/plugins/marketplace.json`:

```json
{
  "name": "helical-internal",
  "interface": { "displayName": "Helical Internal" },
  "plugins": [
    {
      "name": "helical-platform",
      "source": { "source": "local", "path": "./plugins/helical-platform" },
      "policy": {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL"
      },
      "category": "Productivity"
    }
  ]
}
```

Register this non-default repo marketplace, install the preview, restart the desktop app,
and test it in a new task:

```sh
codex plugin marketplace add /absolute/path/to/heli-plugin
codex plugin add helical-platform@helical-internal
```

The marketplace keeps the standard `ON_INSTALL` policy for the eventual authenticated
plugin, but the preview itself requests no configuration or credentials.
