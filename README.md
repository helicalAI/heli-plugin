# Helical Platform plugin

A plugin for Claude Code and Codex that gives you metered access to the
[Helical](https://helical.bio) platform from the chat you're already in: **compute
embeddings** for a single-cell dataset with a foundation model, and **fine-tune** a model
on your own labelled data.

Runs execute on Helical's GPUs and are paid for with the credits in your Helical account.
Nothing starts on its own: every run is priced first and only launches after you approve it.

## Skills

| Skill | What it does |
|---|---|
| `compute-embeddings` | dataset → model → quote → approve → run → outputs |
| `fine-tune-model` | labelled dataset → trained, registered model |
| `check-credits` | reads your balance, explains what a run costs and when you're charged |
| `reconnect-helical` | tells you where to sign in again when the connection drops |

In Claude Code, skills are namespaced by plugin: `/helical-platform:compute-embeddings`
and so on.

## Installing it in Claude Code

From GitHub:

```sh
claude plugin marketplace add helicalAI/heli-plugin   # reads .claude-plugin/marketplace.json
claude plugin install helical-platform@helical-marketplace
```

From a local checkout, use `claude plugin marketplace add ./` instead (a bare `.` is
rejected). The same commands work inside a session as `/plugin marketplace add …` and
`/plugin install …`.

Then run `/mcp` to sign in. Claude Code registers the server as
`plugin:helical-platform:helical`, so from the shell it is
`claude mcp login plugin:helical-platform:helical`.

## Installing it in Codex CLI

```sh
codex plugin marketplace add .   # reads ./.agents/plugins/marketplace.json
codex plugin add helical-platform@helical-marketplace
```

## Signing in

The plugin holds no credentials. `mcp.json` names the remote endpoint
(`https://api.helical.bio/mcp`), and your MCP client signs you in through the browser using
OAuth (Authorization Code + PKCE).

```sh
/mcp        # the select the mcp link to helical and authenticate to open the browser to sign in
```

Your account, credits and usage history live at [console.helical.bio](https://console.helical.bio).

## Layout

```text
.agents/plugins/marketplace.json  # Codex marketplace
.claude-plugin/marketplace.json   # Claude Code marketplace
plugins/helical-platform/
├── plugin.json                 # OpenAI / Codex manifest (Agent Plugins format; listing under extensions.com.openai)
├── .claude-plugin/plugin.json  # Claude Code plugin manifest (same metadata; tests keep them in sync)
├── mcp.json                    # the remote MCP endpoint the plugin connects to (shared)
├── assets/icon.png             # listing icon for both directories
├── skills/
│   ├── check-credits/
│   ├── compute-embeddings/
│   ├── fine-tune-model/
│   └── reconnect-helical/
└── tests/                      # test_plugin_manifest.py
```

## Validate

```sh
make check                         # unit tests, strict manifest validation, install through both loaders
make check TMP_DIR=/tmp/heli       # keep the throwaway Codex / Claude Code config homes there
```

Needs `uv`, `jq`, `codex` and `claude` on the `PATH`. The installs go into a throwaway
config home, never your own `~/.codex` or `~/.claude`.

CI runs `make check`.

When you release, bump `version` in **both** `plugin.json` files: Claude Code only offers
users an update when the version changes.

## License

[AGPL-3.0-or-later](LICENSE), matching the open-source
[`helical`](https://github.com/helicalAI/helical) package.
