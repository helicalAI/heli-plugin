"""Manifest, skill, and documentation consistency.

test_server.py covers the tool contract. This file covers everything around it —
the things that drift silently because nothing executes them:

  * publication metadata (a placeholder URL ships happily)
  * the skill <-> MCP-server wiring (a skill can name a server that does not exist)
  * the local skill's no-tools invariant (see run-helical-locally, DESIGN 7.2)
  * tool names quoted in the skills (a rename leaves the prose stale)
  * tool counts quoted in the docs

Standard library only, matching mcp/server.py — this repo has no dependencies and
no build step, so the suite must run on a bare interpreter.
"""

import importlib.util
import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse


PLUGIN = Path(__file__).parents[1]
REPO = PLUGIN.parents[1]
MANIFEST_PATH = PLUGIN / ".codex-plugin" / "plugin.json"
MCP_JSON_PATH = PLUGIN / ".mcp.json"
SKILLS_DIR = PLUGIN / "skills"

MANIFEST_TEXT = MANIFEST_PATH.read_text()
MANIFEST = json.loads(MANIFEST_TEXT)
MCP_JSON = json.loads(MCP_JSON_PATH.read_text())
SKILL_DIRS = sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())

# The skills that drive the hosted API, and the one that must not.
HOSTED_SKILLS = {"compute-embeddings", "fine-tune-model"}
LOCAL_SKILL = "run-helical-locally"

# Hosts Helical controls. A URL outside this set in shipped metadata is either a
# leftover placeholder or a typo'd domain someone else could register.
ALLOWED_HOSTS = {
    "helical.bio",
    "www.helical.bio",
    "helical-ai.com",
    "www.helical-ai.com",
    "docs.helical-ai.bio",
    "helical.readthedocs.io",
    "github.com",  # path-restricted below
}

# Resolves a written count to an integer. One direction only, deliberately: a
# reverse int -> word lookup would raise KeyError the moment the tool surface
# outgrew the table, turning a documentation-drift failure into a crashed test.
# A claim this cannot resolve (prose like "the tools", or a hyphenated
# "twenty-one") is skipped rather than guessed at.
NUMBER_WORDS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30,
}

_spec = importlib.util.spec_from_file_location(
    "helical_plugin_mcp_manifest_tests", PLUGIN / "mcp" / "server.py"
)
server = importlib.util.module_from_spec(_spec)
assert _spec.loader
_spec.loader.exec_module(server)
TOOL_NAMES = {tool["name"] for tool in server.TOOLS}


def frontmatter(skill_md: Path) -> str:
    """The YAML block between the opening and closing --- of a SKILL.md."""
    text = skill_md.read_text()
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{skill_md} has no frontmatter block"
    return match.group(1)


def agent_config(skill_dir: Path) -> str:
    return (skill_dir / "agents" / "openai.yaml").read_text()


def declared_mcp_servers(yaml_text: str) -> list[str]:
    """MCP server names from `- type: "mcp"` / `value: "<name>"` tool entries.

    A deliberately narrow reader rather than a YAML parse: PyYAML is not
    available (no dependencies), and the only shape that matters is the one the
    agent configs actually use.
    """
    servers = []
    for block in re.split(r"\n\s*-\s+", yaml_text):
        if re.search(r'type:\s*"?mcp"?', block):
            value = re.search(r'value:\s*"?([^"\n]+?)"?\s*$', block, re.M)
            if value:
                servers.append(value.group(1))
    return servers


def tool_calls(text: str) -> set[str]:
    """Names written as a call — `some_tool({ ... })` — anywhere in the text."""
    return set(re.findall(r"\b([a-z][a-z0-9_]{2,})\(\s*\{", text))


def tool_like_tokens(text: str) -> set[str]:
    """Tokens shaped like one of our tool names.

    Matches on the verb prefixes the tool surface actually uses, which keeps
    parameter names (`batch_size`, `model_version`, `s3_key`) out of the result —
    they would otherwise look like stale tool references.
    """
    return set(re.findall(r"\b((?:list|get|read|start|estimate)_[a-z0-9_]+)\b", text))


def tool_mentions(text: str) -> set[str]:
    """Tools referred to anywhere in the text, in any form."""
    return {name for name in TOOL_NAMES if re.search(rf"\b{name}\b", text)}


class ManifestMetadataTests(unittest.TestCase):
    def test_no_placeholder_metadata_survives(self):
        for placeholder in ("example.com", "Example, Inc."):
            self.assertNotIn(placeholder, MANIFEST_TEXT)

    def test_every_url_is_on_a_helical_controlled_host(self):
        """Scans the whole manifest, not just whole quoted values: a docs or
        marketing link inside `longDescription` or `defaultPrompt` is exactly
        where a typo'd domain someone else could register would hide."""
        urls = re.findall(r"https?://[^\s\"'<>)\\]+", MANIFEST_TEXT)
        self.assertGreater(len(urls), 0, "no URLs found — the scan is broken, not the manifest")
        for url in urls:
            host = urlparse(url).netloc.lower()
            with self.subTest(url=url):
                self.assertIn(host, ALLOWED_HOSTS)
                if host == "github.com":
                    self.assertTrue(
                        urlparse(url).path.startswith("/helicalAI/"),
                        f"{url} is not under the helicalAI org",
                    )

    def test_license_claim_agrees_with_what_the_repo_ships(self):
        """Claiming a licence with no LICENSE file is unenforceable, and shipping
        one without declaring it hides it. Either both or neither — and when both,
        they must name the same licence, or the manifest misreports the terms.
        """
        found = [
            path
            for root in (REPO, PLUGIN)
            for name in ("LICENSE", "LICENSE.md", "LICENSE.txt")
            if (path := root / name).exists()
        ]
        declared = MANIFEST.get("license")
        self.assertNotEqual(declared, "", "an empty license string declares nothing")
        self.assertEqual(
            bool(found),
            declared is not None,
            "add a LICENSE file, or drop the manifest's license claim — "
            f"files present: {[str(p.relative_to(REPO)) for p in found]}, "
            f"manifest declares: {declared!r}",
        )
        if found and declared:
            # Compare on the distinguishing word, so "MIT" matches "MIT License"
            # but not an Apache or AGPL text.
            body = found[0].read_text().lower()
            token = declared.lower().split("-")[0]
            self.assertIn(
                token,
                body,
                f"manifest declares {declared!r} but {found[0].name} does not mention it",
            )

    def test_pointers_resolve(self):
        self.assertTrue((PLUGIN / MANIFEST["skills"]).is_dir())
        self.assertTrue((PLUGIN / MANIFEST["mcpServers"]).is_file())

    def test_no_skill_is_left_without_a_default_prompt(self):
        """A cardinality tripwire only: it cannot tell which skill a prompt names,
        so per-skill discoverability is covered by the agent-config check below."""
        self.assertGreaterEqual(
            len(MANIFEST["interface"]["defaultPrompt"]),
            len(SKILL_DIRS),
            "fewer defaultPrompt entries than skills — at least one is undiscoverable",
        )


class SkillStructureTests(unittest.TestCase):
    def test_there_are_skills_to_check(self):
        self.assertEqual({p.name for p in SKILL_DIRS}, HOSTED_SKILLS | {LOCAL_SKILL})

    def test_frontmatter_name_matches_the_directory(self):
        for skill_dir in SKILL_DIRS:
            with self.subTest(skill=skill_dir.name):
                declared = re.search(r"^name:\s*(\S+)", frontmatter(skill_dir / "SKILL.md"), re.M)
                self.assertIsNotNone(declared)
                self.assertEqual(declared.group(1), skill_dir.name)

    def test_every_skill_describes_itself(self):
        for skill_dir in SKILL_DIRS:
            with self.subTest(skill=skill_dir.name):
                self.assertRegex(
                    frontmatter(skill_dir / "SKILL.md"),
                    re.compile(r"^description:\s*\S", re.M),
                    "description: is present but empty",
                )

    def test_every_skill_has_an_agent_config_naming_itself(self):
        for skill_dir in SKILL_DIRS:
            with self.subTest(skill=skill_dir.name):
                config = agent_config(skill_dir)
                self.assertIn(f"${skill_dir.name}", config)


class ToolWiringTests(unittest.TestCase):
    """The wiring that has no runtime check anywhere."""

    def test_declared_mcp_servers_exist_in_mcp_json(self):
        available = set(MCP_JSON["mcpServers"])
        for skill_dir in SKILL_DIRS:
            for name in declared_mcp_servers(agent_config(skill_dir)):
                with self.subTest(skill=skill_dir.name, server=name):
                    self.assertIn(name, available)

    def test_hosted_skills_declare_the_helical_server(self):
        for name in sorted(HOSTED_SKILLS):
            with self.subTest(skill=name):
                self.assertEqual(declared_mcp_servers(agent_config(SKILLS_DIR / name)), ["helical"])

    def test_the_local_skill_declares_no_tools_at_all(self):
        """DESIGN 7.2: our server runs on our infrastructure and cannot execute
        anything on the user's machine. Local mode works only because the agent
        host already has a shell. Declaring an MCP dependency here would promise
        a capability that does not exist."""
        config = agent_config(SKILLS_DIR / LOCAL_SKILL)
        self.assertEqual(declared_mcp_servers(config), [])
        self.assertNotIn("dependencies:", config)

    def test_the_local_skill_references_no_hosted_tool(self):
        """Any mention, not just a call: `list_datasets` in backticks would read
        as an instruction to the agent just as much as `list_datasets({...})`."""
        text = (SKILLS_DIR / LOCAL_SKILL / "SKILL.md").read_text()
        self.assertEqual(tool_mentions(text), set())

    def test_every_tool_name_matches_the_prefix_convention(self):
        """`tool_like_tokens` keys off these verbs, so a tool added under a new
        one would be invisible to the check below. Fail here instead."""
        for name in sorted(TOOL_NAMES):
            with self.subTest(tool=name):
                self.assertTrue(
                    tool_like_tokens(name),
                    f"{name} uses a verb prefix tool_like_tokens does not know",
                )

    def test_tool_names_quoted_in_the_skills_all_exist(self):
        """A renamed tool otherwise leaves instructions that cannot be followed.

        Two readers, because either alone has a gap: the prefix scan catches bare
        references (`start_embedding_run` in prose) but only under known verbs,
        while the call-form scan catches any verb (`cancel_run({...})`) but only
        when written as a call.
        """
        for skill_dir in SKILL_DIRS:
            if skill_dir.name == LOCAL_SKILL:
                continue  # references the helical package's own functions, not our tools
            text = (skill_dir / "SKILL.md").read_text()
            for call in sorted(tool_like_tokens(text) | tool_calls(text)):
                with self.subTest(skill=skill_dir.name, tool=call):
                    self.assertIn(call, TOOL_NAMES)

    def test_the_hosted_skills_between_them_document_every_tool(self):
        documented = set()
        for name in HOSTED_SKILLS:
            documented |= tool_mentions((SKILLS_DIR / name / "SKILL.md").read_text())
        self.assertEqual(
            TOOL_NAMES - documented,
            set(),
            "these tools are exposed but no skill tells the agent when to use them",
        )


class DocumentationDriftTests(unittest.TestCase):
    """Counts written in prose, checked against the code that defines them."""

    def _assert_count_claims(self, path: Path):
        text = path.read_text()
        expected = len(server.TOOLS)
        checked = 0
        # The lookbehind skips section references: "§7 tools" must not read as a
        # claim that seven tools exist.
        for claim in re.findall(r"(?<![§\w-])([\w-]+)\s+tools\b", text):
            normalised = claim.lower()
            stated = int(normalised) if normalised.isdigit() else NUMBER_WORDS.get(normalised)
            if stated is None:
                continue  # prose, not a count
            checked += 1
            with self.subTest(file=path.name, claim=claim):
                self.assertEqual(
                    stated,
                    expected,
                    f"{path.name} says '{claim} tools'; the server defines {expected}",
                )
        return checked

    def test_readme_tool_count(self):
        self.assertGreater(
            self._assert_count_claims(REPO / "README.md"),
            0,
            "README states no tool count this test could check — it passed vacuously",
        )

    def test_plan_tool_count(self):
        self.assertGreater(
            self._assert_count_claims(REPO / "PLAN.md"),
            0,
            "PLAN.md states no tool count this test could check — it passed vacuously",
        )

    def test_design_tool_count(self):
        self.assertGreater(
            self._assert_count_claims(REPO / "DESIGN.md"),
            0,
            "DESIGN.md states no tool count this test could check — it passed vacuously",
        )


if __name__ == "__main__":
    unittest.main()
