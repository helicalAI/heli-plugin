"""Manifest consistency.

Covers the things that drift silently because nothing executes them:

  * shipped URLs (a typo'd domain ships happily)
  * the OpenAI submission limits (an upload accepts what review rejects)
  * the two manifests and the reconnect command (Claude Code and OpenAI read
    different files, so a rename or version bump can reach only one)

Standard library only: this repo has no dependencies and no build step, so the
suite must run on a bare interpreter.
"""

import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse


PLUGIN = Path(__file__).parents[1]
REPO = PLUGIN.parents[1]
MANIFEST_PATH = PLUGIN / "plugin.json"
CLAUDE_MANIFEST_PATH = PLUGIN / ".claude-plugin" / "plugin.json"
CLAUDE_MARKETPLACE_PATH = REPO / ".claude-plugin" / "marketplace.json"
MCP_JSON_PATH = PLUGIN / "mcp.json"
AUTH_SKILL_PATH = PLUGIN / "skills" / "reconnect-helical" / "SKILL.md"

MANIFEST_TEXT = MANIFEST_PATH.read_text()
MANIFEST = json.loads(MANIFEST_TEXT)
OPENAI_INTERFACE = MANIFEST["extensions"]["com.openai"]["interface"]
CLAUDE_MANIFEST_TEXT = CLAUDE_MANIFEST_PATH.read_text()
CLAUDE_MANIFEST = json.loads(CLAUDE_MANIFEST_TEXT)
CLAUDE_MARKETPLACE_TEXT = CLAUDE_MARKETPLACE_PATH.read_text()
MCP_JSON = json.loads(MCP_JSON_PATH.read_text())

# Hosts Helical controls. A URL outside this set in shipped metadata is either a
# leftover placeholder or a typo'd domain someone else could register.
ALLOWED_HOSTS = {
    "helical.bio",
    "www.helical.bio",
    "console.helical.bio",
    "helical-ai.com",
    "www.helical-ai.com",
    "docs.helical-ai.bio",
    "helical.readthedocs.io",
    "github.com",  # path-restricted below
    "datasets.cellxgene.cziscience.com",  # the public file the review cases ingest
    "drive.google.com",  # the review walkthrough video
}


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from a PNG's IHDR chunk, without an imaging library."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


class ManifestMetadataTests(unittest.TestCase):
    def test_every_url_is_on_a_helical_controlled_host(self):
        """Scans the whole manifest, not just whole quoted values: a docs or
        marketing link inside `longDescription` or `defaultPrompt` is exactly
        where a typo'd domain someone else could register would hide."""
        shipped = {
            MANIFEST_PATH: MANIFEST_TEXT,
            CLAUDE_MANIFEST_PATH: CLAUDE_MANIFEST_TEXT,
            CLAUDE_MARKETPLACE_PATH: CLAUDE_MARKETPLACE_TEXT,
        }
        # `$schema` names the format the file follows; nothing links to it.
        urls = [
            (path, url)
            for path, text in shipped.items()
            for url in re.findall(
                r"https?://[^\s\"'<>)\\]+", re.sub(r'"\$schema":\s*"[^"]*"', "", text)
            )
        ]
        self.assertGreater(len(urls), 0, "no URLs found — the scan is broken, not the manifest")
        for path, url in urls:
            host = urlparse(url).netloc.lower()
            with self.subTest(file=path.relative_to(REPO), url=url):
                self.assertIn(host, ALLOWED_HOSTS)
                if host == "github.com":
                    self.assertTrue(
                        urlparse(url).path.startswith("/helicalAI/"),
                        f"{url} is not under the helicalAI org",
                    )


class OpenAIListingTests(unittest.TestCase):
    """The submission limits for `extensions.com.openai`. An upload accepts text
    past them, so nothing fails until the listing is submitted for review."""

    def test_listing_text_fits_the_submission_limits(self):
        for field, limit in (
            ("displayName", 30), ("shortDescription", 30),
            ("longDescription", 4000), ("developerName", 80),
        ):
            with self.subTest(field=field):
                self.assertTrue(OPENAI_INTERFACE[field].strip())
                self.assertLessEqual(len(OPENAI_INTERFACE[field]), limit)

    def test_default_prompts_fit_the_submission_limits(self):
        prompts = OPENAI_INTERFACE["defaultPrompt"]
        self.assertTrue(1 <= len(prompts) <= 3, "the directory shows at most three")
        self.assertEqual(len(set(prompts)), len(prompts))
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertLessEqual(len(prompt), 128)

    def test_review_cases_are_complete_for_mcp_review(self):
        """Initial MCP review takes exactly five positive and three negative cases,
        and a positive case without its tools and expected result cannot be checked.
        Upload accepts partial lists, so only submission would catch this."""
        cases = MANIFEST["extensions"]["com.openai"]["review"]["test_cases"]
        self.assertEqual(len(cases["positive"]), 5)
        self.assertEqual(len(cases["negative"]), 3)
        self.assertEqual(len(MCP_JSON["mcpServers"]), 1, "plugin-level cases need exactly one server")
        for kind, required in (
            ("positive", ("description", "prompt", "tools_triggered", "expected_behavior")),
            ("negative", ("description", "prompt")),
        ):
            for case in cases[kind]:
                with self.subTest(kind=kind, case=case.get("description")):
                    for field in required:
                        self.assertTrue(case.get(field, "").strip(), f"{field} is missing")
        prompts = [c["prompt"] for kind in ("positive", "negative") for c in cases[kind]]
        self.assertEqual(len(set(prompts)), len(prompts))

    def test_no_reviewer_secrets_in_the_package(self):
        """ZIP import rejects these; reviewer access goes through the dashboard form."""
        review = MANIFEST["extensions"]["com.openai"]["review"]
        for field in ("test_credentials", "reviewer_instructions"):
            with self.subTest(field=field):
                self.assertNotIn(field, review)

    def test_icons_resolve_to_square_pngs_inside_the_plugin(self):
        """Both directories take the icon from a file in the plugin. OpenAI accepts
        48 px up; Claude's directory wants 512 to 2048, so that is the range held."""
        icons = {
            "composerIcon": OPENAI_INTERFACE["composerIcon"],
            "logo": OPENAI_INTERFACE["logo"],
            "claude icon": CLAUDE_MANIFEST["icon"],
        }
        for field, rel in icons.items():
            with self.subTest(field=field):
                self.assertTrue(rel.startswith("./"), "paths must be ./-relative")
                path = (PLUGIN / rel).resolve()
                self.assertTrue(path.is_relative_to(PLUGIN.resolve()))
                width, height = png_size(path)
                self.assertEqual(width, height)
                self.assertTrue(512 <= width <= 2048)


class ClientParityTests(unittest.TestCase):
    """OpenAI and Codex read the portable root `plugin.json`; Claude Code reads only
    `.claude-plugin/plugin.json`. Both share `skills/` and `mcp.json`. The
    duplicated metadata is what drifts: bump one version and not the other, and the
    clients install different releases under the same number."""

    SHARED_FIELDS = (
        "name", "version", "description", "author",
        "homepage", "repository", "license", "keywords",
    )

    def test_the_two_manifests_agree_on_shared_metadata(self):
        for field in self.SHARED_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(CLAUDE_MANIFEST.get(field), MANIFEST.get(field))

    def test_the_claude_manifest_points_at_the_shared_mcp_config(self):
        """Claude Code looks for `.mcp.json` by default, so without this pointer it
        would load no server. `skills/` it finds on its own."""
        self.assertEqual(CLAUDE_MANIFEST.get("mcpServers"), "./mcp.json")
        self.assertNotIn("skills", CLAUDE_MANIFEST)
        self.assertFalse((PLUGIN / ".mcp.json").exists(), "a second MCP config would drift")

    def test_the_auth_skill_names_the_server_as_claude_code_registers_it(self):
        """A plugin-provided server is registered as `plugin:<plugin>:<server>`, and
        `claude mcp login <server>` answers "No MCP server named ..." for it. Renaming
        the plugin or the server leaves the reconnect command pointing at nothing."""
        text = AUTH_SKILL_PATH.read_text()
        for server in MCP_JSON["mcpServers"]:
            with self.subTest(server=server):
                self.assertIn(
                    f"claude mcp login plugin:{CLAUDE_MANIFEST['name']}:{server}", text
                )


if __name__ == "__main__":
    unittest.main()
