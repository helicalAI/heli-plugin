.PHONY: check test lint round-trip-codex round-trip-claude

# Where the round-trip installs put their Codex and Claude Code config homes, so they
# never touch ~/.codex or ~/.claude. Created if missing; with no value, `check` makes a
# fresh one under $TMPDIR and removes it on success (kept on failure, for inspection).
#   Usage: make check TMP_DIR=/tmp/heli-plugin-validate
TMP_DIR ?=

# Full pre-release gate: unit tests, strict manifest validation, then install the plugin
# through both loaders. This is what .github/workflows/ci.yml runs.
check:
	@set -eu; \
	if [ -n "$(TMP_DIR)" ]; then tmp="$(TMP_DIR)"; owned=0; \
	else tmp=$$(mktemp -d -t heli-plugin-validate.XXXXXX); owned=1; fi; \
	mkdir -p "$$tmp"; \
	echo "Using TMP_DIR=$$tmp"; \
	$(MAKE) --no-print-directory test lint round-trip-codex round-trip-claude TMP_DIR="$$tmp" \
		|| { echo "Validation failed; left $$tmp for inspection"; exit 1; }; \
	if [ "$$owned" = 1 ]; then rm -rf "$$tmp"; fi

# Standard library only, so any interpreter works; uv picks one without a project env.
test:
	uv run python -m unittest discover -s plugins/helical-platform/tests -v

# --strict fails on unrecognized fields and missing metadata, which the loader itself
# tolerates silently.
lint:
	claude plugin validate --strict .
	claude plugin validate --strict plugins/helical-platform

# The round-trips below need TMP_DIR; run them through `check`, or pass it yourself.
# Each wipes only its own subdirectory, so a reused TMP_DIR starts from a clean install.
# Assert on `installed`: a local marketplace reports `available: []` even on success, so
# an emptiness check there would pass vacuously.
round-trip-codex:
	@test -n "$(TMP_DIR)" || { echo "TMP_DIR is required (or run 'make check')"; exit 1; }
	rm -rf "$(TMP_DIR)/codex" && mkdir -p "$(TMP_DIR)/codex"
	@set -eu; export CODEX_HOME="$(TMP_DIR)/codex"; \
	expected=$$(jq -re .version plugins/helical-platform/plugin.json); \
	codex plugin marketplace add . --json > /dev/null; \
	codex plugin add helical-platform@helical-marketplace; \
	codex plugin list --json | jq -e --arg v "$$expected" \
		'.installed | map(select(.name == "helical-platform" and .version == $$v)) | length == 1' \
		> /dev/null; \
	echo "Codex installed helical-platform $$expected"

# A bare `.` is rejected as a marketplace source, hence `./`.
round-trip-claude:
	@test -n "$(TMP_DIR)" || { echo "TMP_DIR is required (or run 'make check')"; exit 1; }
	rm -rf "$(TMP_DIR)/claude" && mkdir -p "$(TMP_DIR)/claude"
	@set -eu; export CLAUDE_CONFIG_DIR="$(TMP_DIR)/claude"; \
	expected=$$(jq -re .version plugins/helical-platform/.claude-plugin/plugin.json); \
	claude plugin marketplace add ./; \
	claude plugin install helical-platform@helical-marketplace; \
	claude plugin list --json | jq -e --arg v "$$expected" \
		'map(select(.id == "helical-platform@helical-marketplace" and .version == $$v)) | length == 1' \
		> /dev/null; \
	echo "Claude Code installed helical-platform $$expected"
