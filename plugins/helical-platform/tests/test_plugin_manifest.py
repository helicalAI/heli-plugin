"""Manifest and skill consistency.

Covers the things that drift silently because nothing executes them:

  * publication metadata (a placeholder URL ships happily)
  * the skill <-> MCP-server wiring (a skill can name a server that does not exist)
  * the local skill's no-tools invariant (see run-helical-locally, DESIGN 7.2)
  * the auth skill's endpoint agreement (a moved endpoint leaves it reconnecting
    against a host the client no longer talks to)

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
CODEX_MARKETPLACE_PATH = REPO / ".agents" / "plugins" / "marketplace.json"
CLAUDE_MARKETPLACE_PATH = REPO / ".claude-plugin" / "marketplace.json"
MCP_JSON_PATH = PLUGIN / "mcp.json"
SKILLS_DIR = PLUGIN / "skills"

MANIFEST_TEXT = MANIFEST_PATH.read_text()
MANIFEST = json.loads(MANIFEST_TEXT)
OPENAI_INTERFACE = MANIFEST["extensions"]["com.openai"]["interface"]
CLAUDE_MANIFEST_TEXT = CLAUDE_MANIFEST_PATH.read_text()
CLAUDE_MANIFEST = json.loads(CLAUDE_MANIFEST_TEXT)
CODEX_MARKETPLACE = json.loads(CODEX_MARKETPLACE_PATH.read_text())
CLAUDE_MARKETPLACE_TEXT = CLAUDE_MARKETPLACE_PATH.read_text()
CLAUDE_MARKETPLACE = json.loads(CLAUDE_MARKETPLACE_TEXT)
MCP_JSON = json.loads(MCP_JSON_PATH.read_text())
SKILL_DIRS = sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())

# The skills that drive the hosted API, the one that must not, the one that answers
# from the site's own pages because no tool here reads a credit balance, and the one
# that runs when the server cannot be reached at all.
HOSTED_SKILLS = {"compute-embeddings", "fine-tune-model"}
LOCAL_SKILL = "run-helical-locally"
SITE_SKILL = "check-credits"
AUTH_SKILL = "reconnect-helical"

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
}


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from a PNG's IHDR chunk, without an imaging library."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


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


def helical_hosts(text: str) -> set[str]:
    """Helical hostnames named anywhere in the text, however they are written —
    a bare `console.helical.bio` as readily as a full URL."""
    return set(re.findall(r"\b[a-z0-9][a-z0-9-]*\.helical\.bio\b", text))


class ManifestMetadataTests(unittest.TestCase):
    def test_no_placeholder_metadata_survives(self):
        for text in (MANIFEST_TEXT, CLAUDE_MANIFEST_TEXT):
            for placeholder in ("example.com", "Example, Inc."):
                self.assertNotIn(placeholder, text)

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
            # but not an Apache or proprietary text. Word-bounded, not a substring:
            # "MIT" occurs inside "permitted" and "transmit", both of which appear
            # in ordinary licence boilerplate, so a substring test would accept
            # almost any declaration against almost any file.
            body = found[0].read_text().lower()
            token = re.escape(declared.lower().split("-")[0])
            self.assertRegex(
                body,
                re.compile(rf"\b{token}\b"),
                f"manifest declares {declared!r} but {found[0].name} does not name it",
            )

    def test_portable_components_are_where_hosts_look(self):
        """The portable format has no pointers: hosts read these fixed paths."""
        self.assertTrue(SKILLS_DIR.is_dir())
        self.assertTrue(MCP_JSON_PATH.is_file())



class OpenAIListingTests(unittest.TestCase):
    """The submission limits for `extensions.com.openai.interface`. An upload accepts
    text past them, so nothing fails until the listing is submitted for review."""

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

    def test_mcp_review_links_are_all_https(self):
        """An MCP app needs all four for public review; homepage and author.url
        do not stand in for them."""
        for field in ("websiteURL", "supportURL", "privacyPolicyURL", "termsOfServiceURL"):
            with self.subTest(field=field):
                self.assertEqual(urlparse(OPENAI_INTERFACE[field]).scheme, "https")

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


class SkillStructureTests(unittest.TestCase):
    def test_there_are_skills_to_check(self):
        self.assertEqual(
            {p.name for p in SKILL_DIRS},
            HOSTED_SKILLS | {LOCAL_SKILL, SITE_SKILL, AUTH_SKILL},
        )

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

    def test_the_site_skill_declares_no_tools(self):
        """It answers with the site's own routes because there is nothing to call:
        no tool on this surface reports a credit balance. That surface is served
        remotely now, so this repo cannot assert a balance tool has not appeared —
        only that the skill still reaches for none."""
        self.assertEqual(declared_mcp_servers(agent_config(SKILLS_DIR / SITE_SKILL)), [])

    def test_the_auth_skill_declares_no_tools_and_names_no_url_of_its_own(self):
        """It runs precisely when the server is unreachable, so declaring a dependency
        on it would gate the recovery path on the thing that is broken. And the client
        discovers where to authorize from the endpoint's own `WWW-Authenticate`
        challenge, so an OAuth endpoint written out here as a full link is one the
        agent would copy: completing it authorizes whichever client assembled it and
        leaves the host's stored credentials — the ones the plugin actually uses —
        untouched. Naming the endpoints in prose is fine; a clickable one is not,
        which is why this matches only a scheme-and-host form."""
        config = agent_config(SKILLS_DIR / AUTH_SKILL)
        self.assertEqual(declared_mcp_servers(config), [])
        text = (SKILLS_DIR / AUTH_SKILL / "SKILL.md").read_text()
        self.assertNotRegex(
            text,
            re.compile(r"https?://\S*/(authorize|token|register)\b"),
            "the skill spells out an OAuth request URL — it must point at the client's "
            "own reconnect command instead",
        )

    def test_the_auth_skill_routes_on_every_code_it_handles(self):
        """Implicit invocation is matched against the frontmatter description, so a
        failure code handled in the body but absent from the description reaches nobody:
        the agent never loads the skill that knows what to do with it. Hosts paraphrase
        these codes and drop the OAuth `error_description`, which is why the body has to
        list them at all."""
        skill_md = SKILLS_DIR / AUTH_SKILL / "SKILL.md"
        body = skill_md.read_text().split("\n---\n", 1)[1]
        described = frontmatter(skill_md)
        for code in re.findall(r"\binvalid_[a-z]+|\bunauthorized_client\b", body):
            with self.subTest(code=code):
                self.assertIn(
                    code,
                    described,
                    f"{code} is handled but not described — implicit invocation cannot "
                    "route a failure the description does not mention",
                )

    def test_the_auth_skill_agrees_with_the_endpoint_it_reconnects_against(self):
        """The reconnect instructions name a host and a server name. Both come from
        `mcp.json`, and neither is derivable at runtime, so moving the endpoint or
        renaming the server silently leaves the skill telling users to reauthorize
        somewhere the client no longer talks to."""
        text = (SKILLS_DIR / AUTH_SKILL / "SKILL.md").read_text()
        for name, server in MCP_JSON["mcpServers"].items():
            with self.subTest(server=name):
                self.assertRegex(text, re.compile(rf"\b{re.escape(name)}\b"))
                url = server.get("url")
                if url is None:
                    continue  # a STDIO server has no endpoint to reauthorize against
                self.assertIn(
                    urlparse(url).netloc, text, f"the skill does not name {url}"
                )

    def test_the_skills_agree_on_where_signing_in_happens(self):
        """Two skills sending users to two different Helical addresses guarantees one of
        them is wrong, and the wrong one costs a paying customer a dead end. The auth
        skill may name the MCP endpoint (it explains discovery) and the account site that
        check-credits already established — nothing else."""
        endpoints = {
            urlparse(server["url"]).netloc
            for server in MCP_JSON["mcpServers"].values()
            if server.get("url")
        }
        site = helical_hosts((SKILLS_DIR / SITE_SKILL / "SKILL.md").read_text())
        auth = helical_hosts((SKILLS_DIR / AUTH_SKILL / "SKILL.md").read_text())
        self.assertTrue(site, "check-credits names no account site — the scan is broken")
        self.assertTrue(
            site & auth,
            f"{AUTH_SKILL} must send users to the same account site as {SITE_SKILL} "
            f"({sorted(site)}), not {sorted(auth)}",
        )
        self.assertEqual(
            auth - endpoints - site,
            set(),
            "the auth skill invents a Helical host no other skill knows about",
        )


class ClientParityTests(unittest.TestCase):
    """OpenAI and Codex read the portable root `plugin.json`; Claude Code reads only
    `.claude-plugin/plugin.json`. Each client has its own marketplace, and all of
    them share `skills/` and `mcp.json`. The duplicated metadata is what drifts:
    bump one version and not the other, and the clients install different
    releases under the same number."""

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

    def test_the_two_marketplaces_ship_the_same_plugin_from_the_same_path(self):
        self.assertEqual(CLAUDE_MARKETPLACE["name"], CODEX_MARKETPLACE["name"])
        codex = {p["name"]: p["source"]["path"] for p in CODEX_MARKETPLACE["plugins"]}
        claude = {p["name"]: p["source"] for p in CLAUDE_MARKETPLACE["plugins"]}
        self.assertEqual(claude, codex)
        self.assertEqual((REPO / claude[MANIFEST["name"]]).resolve(), PLUGIN.resolve())

    def test_the_auth_skill_names_the_server_as_claude_code_registers_it(self):
        """A plugin-provided server is registered as `plugin:<plugin>:<server>`, and
        `claude mcp login <server>` answers "No MCP server named ..." for it. Renaming
        the plugin or the server leaves the reconnect command pointing at nothing."""
        text = (SKILLS_DIR / AUTH_SKILL / "SKILL.md").read_text()
        for server in MCP_JSON["mcpServers"]:
            with self.subTest(server=server):
                self.assertIn(
                    f"claude mcp login plugin:{CLAUDE_MANIFEST['name']}:{server}", text
                )


if __name__ == "__main__":
    unittest.main()
