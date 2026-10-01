# Developing

## Branches

| Branch | What it is | Who reads it |
|---|---|---|
| `develop` | Integration branch and the GitHub default. Every PR targets it. | Contributors |
| `main` | Production. Only `develop` is merged into it, and every merge is a release. | The Anthropic Directory, the README's GitHub installs (`#main` / `--ref main`) and the release workflow |

Never commit or push to `main` directly. Branch from `develop` and open PRs into
`develop`.

## Daily loop

```sh
git switch develop && git pull
git switch -c <your-branch>
# ... change things ...
make check                      # unit tests, strict validation, install through both loaders
```

Then open a PR into `develop`. CI (`ci.yml`) runs `make check` on it.

## Releasing

1. **Bump the version** on `develop`, in **both** `plugins/<name>/plugin.json` and
   `plugins/<name>/.claude-plugin/plugin.json`. The tests fail if they differ. Claude
   Code only offers users an update when the version changes.
2. **Open a PR from `develop` into `main`.** The `version-check` job runs
   `make release-plan` and fails if a plugin changed since its last release without a
   version bump. For every plugin it would release, the `stage` job builds
   `<name>-<version>.zip` and posts a comment on the PR with a download link and its
   SHA-256. The comment is updated on every push to the PR.
3. **Upload the zip to OpenAI before merging (manual).** Download it from the PR
   comment and upload it in the OpenAI Developer Platform as the plugin's new package,
   with release notes. **Merge only once OpenAI's upload checks pass.** If OpenAI
   rejects it, fix the plugin on `develop` and push: nothing is tagged or released yet,
   so no version bump is needed, and the comment gets the new zip. Nothing enforces
   this step; the reviewer checks it before merging. A new zip is needed only when
   `plugin.json`, `skills/`, `assets/` or `mcp.json` changed. Changes to the hosted
   MCP server do not need one; OpenAI rescans the server.
4. **Merge it.** `release.yml` runs `make check`, then for every plugin with a new
   version creates the tag `<name>-v<version>` and a GitHub Release with
   `<name>-<version>.zip` attached. It is the same zip, byte for byte, as the one
   uploaded from the PR: entries are stamped with the plugin's last change, not the
   build time. A merge that touches no plugin files releases nothing.
5. **Anthropic: nothing to do.** The directory follows `main`, scans the new commit and
   publishes it if it passes. Check the result in the developer portal's Versions tab.
   The previous version stays live until the new one is published.

If the `release` job fails partway, re-run it: plugins already tagged are skipped.

## Packaging locally

```sh
make package          # dist/<name>-<version>.zip per plugin
make release-plan     # which plugins a merge to main would release (fetches tags from origin)
```

- The zips are built with `git archive` from the **committed `HEAD`**. Uncommitted
  changes are not packaged; `make package` warns when there are some.
- Every folder under `plugins/` is treated as a plugin and must have a root
  `plugin.json`; the commands fail otherwise. Files directly under `plugins/` are
  ignored.
- Each zip holds one top-level `<name>/` directory: everything tracked under
  `plugins/<name>/` except `tests/`. The exclusion list is `EXCLUDED` in
  `scripts/release.py`.
- `make package` fails on what OpenAI would reject: files beside the plugin directory,
  a missing `plugin.json` or referenced asset, a file under `skills/` that is not a
  skill directory with a `SKILL.md`, symlinks, more than 5,000 entries or more than
  100 MB.

## One-time setup to verify

- The Anthropic Directory submission's **"Branch or tag"** field is set to `main`. Left
  blank, it follows the default branch, which is `develop`.
- Recommended: make the `version-check` and `ci` checks required for PRs into `main`
  in the GitHub branch protection settings.

## Possible later additions

- Build provenance with `actions/attest`, verifiable with `gh attestation verify`.
- GitHub Immutable Releases, so a published tag and its assets cannot be changed.
- Generated release notes (`gh release create --generate-notes`).
