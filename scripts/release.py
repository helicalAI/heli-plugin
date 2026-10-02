"""Release tooling: package each plugin as an OpenAI submission zip, and decide
which plugins a merge to main releases. See DEVELOPING.md.

Standard library only, like the test suite: the repo has no dependencies.
"""

import argparse
import json
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Left out of every zip: OpenAI does not read them. This has to be a pathspec on
# `git archive`, because a root .gitattributes export-ignore does not apply when
# archiving a subtree.
EXCLUDED = ("tests",)

# OpenAI's limits for a plugin ZIP ("Plugin submission errors" in their docs).
MAX_ZIP_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRIES = 5000

# The version becomes part of a git tag, so it has to be a plain semantic version.
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")


class ReleaseError(Exception):
    """A problem the person running the release has to fix."""


class GitError(ReleaseError):
    """A git command failed; the message carries git's own stderr."""


@dataclass(frozen=True)
class Plugin:
    name: str
    version: str
    path: str  # repo-relative, e.g. "plugins/helical-platform"

    @property
    def tag(self) -> str:
        return f"{self.name}-v{self.version}"

    @property
    def zip_name(self) -> str:
        return f"{self.name}-{self.version}.zip"


def git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
        ).stdout
    except subprocess.CalledProcessError as e:
        raise GitError(f"git {' '.join(args)}: {e.stderr.strip()}") from e


def discover_plugins(repo: Path, ref: str = "HEAD") -> list[Plugin]:
    """Every folder under plugins/ in the committed tree at `ref`; files there are
    ignored. That tree is what gets packaged, so reading the working tree instead
    would name a zip after an uncommitted version bump."""
    plugins = []
    for directory in git(repo, "ls-tree", "-d", "--name-only", f"{ref}:plugins").splitlines():
        path = f"plugins/{directory}"
        try:
            text = git(repo, "show", f"{ref}:{path}/plugin.json")
        except GitError as e:
            # Skipping it would leave the plugin unpackaged and its version unchecked.
            raise ReleaseError(
                f"{path}/ has no plugin.json; every folder under plugins/ must be a plugin"
            ) from e
        try:
            data = json.loads(text)
            plugin = Plugin(data["name"], data["version"], path)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise ReleaseError(f'{path}/plugin.json needs "name" and "version": {e}') from e
        if not SEMVER.fullmatch(plugin.version):
            raise ReleaseError(
                f"{path}/plugin.json: version {plugin.version!r} is not a semantic version"
            )
        plugins.append(plugin)
    if not plugins:
        raise ReleaseError(f"no plugins/*/plugin.json at {ref}")
    return plugins


def build_zip(repo: Path, plugin: Plugin, dist: Path, ref: str = "HEAD") -> Path:
    """`git archive` takes only tracked files, so caches and local .env files never
    reach the zip. Archiving a tree rather than a commit stamps entries with the
    build time, so --mtime pins them to the last commit that changed the plugin.
    The zip uploaded to OpenAI from the PR and the one released after the merge are
    built on different commits, but that last change is the same, so they are the
    same bytes."""
    out = dist / plugin.zip_name
    committed = git(repo, "log", "-1", "--format=%cI", ref, "--", plugin.path).strip()
    git(
        repo, "archive", "--format=zip", f"--prefix={plugin.name}/", f"--mtime={committed}",
        "-o", str(out), f"{ref}:{plugin.path}", *(f":!{excluded}" for excluded in EXCLUDED),
    )
    return out


def asset_refs(value: object) -> set[str]:
    """Every "./assets/..." string anywhere in a manifest."""
    if isinstance(value, str):
        return {value} if value.startswith("./assets/") else set()
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return {ref for item in value for ref in asset_refs(item)}
    return set()


def check_zip(path: Path, name: str) -> list[str]:
    """What OpenAI's validator would reject, or what means the wrong files were
    packaged. Empty when the zip is fine."""
    problems = []
    if path.stat().st_size > MAX_ZIP_BYTES:
        problems.append(f"larger than {MAX_ZIP_BYTES // (1024 * 1024)} MB")
    root = f"{name}/"
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        names = {info.filename for info in infos}
        if len(infos) > MAX_ZIP_ENTRIES:
            problems.append(f"{len(infos)} entries, more than {MAX_ZIP_ENTRIES}")
        beside = sorted(n for n in names if not n.startswith(root))
        if beside:
            problems.append(f"entries beside {root}: {', '.join(beside)}")
        if f"{root}plugin.json" not in names:
            problems.append(f"{root}plugin.json is missing")
        else:
            manifest = json.loads(zf.read(f"{root}plugin.json"))
            for ref in sorted(asset_refs(manifest)):
                if root + ref.removeprefix("./") not in names:
                    problems.append(f"plugin.json references {ref}, which is not in the zip")
        for excluded in EXCLUDED:
            if any(n.startswith(f"{root}{excluded}/") for n in names):
                problems.append(f"{root}{excluded}/ should not be packaged")
        # The high 16 bits of external_attr hold the Unix st_mode; 0o170000 masks the file type, 0o120000 is a symlink.
        links = sorted(i.filename for i in infos if (i.external_attr >> 16) & 0o170000 == 0o120000)
        if links:
            problems.append(f"symlinks are rejected by OpenAI: {', '.join(links)}")
        skills = f"{root}skills/"
        children = {n[len(skills):].split("/", 1)[0] for n in names if n.startswith(skills)} - {""}
        for child in sorted(children):
            if f"{skills}{child}/SKILL.md" not in names:
                problems.append(f"{skills}{child} is not a skill directory with a SKILL.md")
    return problems


def needs_release(repo: Path, plugin: Plugin, ref: str = "HEAD") -> bool:
    """True when the plugin's tag does not exist yet; False when it exists and the
    plugin is unchanged since. A change under the same version raises: the Anthropic
    Directory would ship it under a version number that is already released."""
    tagged = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--quiet", "--verify", f"refs/tags/{plugin.tag}"],
        capture_output=True,
    ).returncode == 0
    if not tagged:
        return True
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--quiet", plugin.tag, ref, "--", plugin.path],
        capture_output=True, text=True,
    )
    if diff.returncode == 0:
        return False
    if diff.returncode == 1:
        raise ReleaseError(
            f"{plugin.name} changed since {plugin.tag} but version is still "
            f"{plugin.version}; bump version in both plugin.json files"
        )
    raise ReleaseError(f"git diff {plugin.tag} {ref} failed: {diff.stderr.strip()}")


def create_release_plan(
    repo: Path, dist: Path, ref: str = "HEAD", fetch: bool = False
) -> list[Plugin]:
    """The plugins to release, also written to dist/release-plan as "<name> <version>"
    lines for the release job. Raises, and writes no plan, when a plugin changed
    without a version bump. Fetching first means a clone missing a tag that origin
    has cannot pass a check CI would fail.

    Only the plan is written here; the release job in release.yml reads it and runs
    `gh release create`. Nothing in this script publishes, so running it on a
    developer's machine can never create a tag or a release."""
    if fetch:
        git(repo, "fetch", "--quiet", "--tags", "origin")
    to_release, problems = [], []
    for plugin in discover_plugins(repo, ref):
        try:
            if needs_release(repo, plugin, ref):
                to_release.append(plugin)
        except ReleaseError as e:
            problems.append(str(e))
    if problems:
        raise ReleaseError("\n".join(problems))
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "release-plan").write_text(
        "".join(f"{plugin.name} {plugin.version}\n" for plugin in to_release)
    )
    return to_release


def package(repo: Path, dist: Path, ref: str = "HEAD") -> list[Path]:
    """One zip per plugin, each checked against OpenAI's rules. No checksum file:
    GitHub computes and shows a SHA-256 for every release asset. Earlier zips are
    removed first so a previous version never ships; dist/release-plan is left for
    the release job."""
    dist.mkdir(parents=True, exist_ok=True)
    for stale in dist.glob("*.zip"):
        stale.unlink()
    zips, problems = [], []
    for plugin in discover_plugins(repo, ref):
        path = build_zip(repo, plugin, dist, ref)
        zips.append(path)
        problems += [f"{path.name}: {problem}" for problem in check_zip(path, plugin.name)]
    if problems:
        raise ReleaseError("\n".join(problems))
    return sorted(zips)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package plugins and gate releases.")
    # Only for tests/test_release.py, which points the CLI at a throwaway repo and
    # dist/. Hidden from --help: in real use both always default to this checkout.
    parser.add_argument("--repo", type=Path, default=REPO, help=argparse.SUPPRESS)
    parser.add_argument("--dist", type=Path, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("package", help="build dist/<name>-<version>.zip per plugin")
    plan_command = commands.add_parser(
        "release-plan",
        help="write dist/release-plan; fails if a plugin changed without a version bump",
    )
    plan_command.add_argument("--fetch", action="store_true", help="fetch tags from origin first")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    dist = (args.dist or repo / "dist").resolve()
    try:
        if args.command == "package":
            if git(repo, "status", "--porcelain", "--", "plugins").strip():
                print("warning: uncommitted changes under plugins/ are not packaged; "
                      "the zips are built from HEAD", file=sys.stderr)
            for path in package(repo, dist):
                print(f"packaged {path}")
        elif args.command == "release-plan":
            plan = create_release_plan(repo, dist, fetch=args.fetch)
            for plugin in plan:
                print(f"release {plugin.tag}")
            if not plan:
                print("nothing to release")
    except ReleaseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
