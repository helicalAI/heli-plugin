"""Release tooling: the zips uploaded to OpenAI and the version gate on main.

Each test builds a throwaway git repo, because both commands read the committed
tree and its tags, not the working directory.

Standard library only, like the plugin suite.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import release  # noqa: E402

# The developer's own git config (signing, hooks, templates) must not leak into
# the fixture commits.
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    # A commit time far from today, so a zip stamped with the build time shows.
    "GIT_AUTHOR_DATE": "2001-06-15T12:00:00Z",
    "GIT_COMMITTER_DATE": "2001-06-15T12:00:00Z",
}


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=GIT_ENV,
    ).stdout


def write_plugin(repo: Path, name: str = "demo", version: str = "1.0.0") -> Path:
    """A minimal plugin shaped like plugins/helical-platform."""
    root = repo / "plugins" / name
    interface = {"logo": "./assets/icon.png", "composerIcon": "./assets/icon.png"}
    files = {
        "plugin.json": json.dumps(
            {"name": name, "version": version,
             "extensions": {"com.openai": {"interface": interface}}}
        ),
        ".claude-plugin/plugin.json": json.dumps({"name": name, "version": version}),
        "mcp.json": "{}",
        "assets/icon.png": "png",
        "skills/hello/SKILL.md": "# hello",
        "tests/test_demo.py": "",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def commit(repo: Path, message: str = "change", date: str | None = None) -> None:
    run_git(repo, "add", "-A")
    if date is None:
        run_git(repo, "commit", "-q", "-m", message)
    else:
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", message], check=True,
            env={**GIT_ENV, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
        )


class GitRepoTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        run_git(self.repo, "init", "-q", "-b", "main")
        write_plugin(self.repo)
        commit(self.repo, "initial")
        self.dist = self.tmp / "dist"

    def zip_names(self, path: Path) -> set[str]:
        with zipfile.ZipFile(path) as zf:
            return set(zf.namelist())


class PackageTests(GitRepoTestCase):
    def test_writes_one_zip_per_plugin_named_after_its_version(self):
        write_plugin(self.repo, "other", "2.0.0")
        commit(self.repo)
        release.package(self.repo, self.dist)
        self.assertEqual(
            {p.name for p in self.dist.glob("*.zip")},
            {"demo-1.0.0.zip", "other-2.0.0.zip"},
        )

    def test_zip_holds_one_plugin_root_and_leaves_out_tests(self):
        [path] = release.package(self.repo, self.dist)
        names = self.zip_names(path)
        self.assertTrue(all(n.startswith("demo/") for n in names), names)
        for kept in ("demo/plugin.json", "demo/.claude-plugin/plugin.json",
                     "demo/mcp.json", "demo/assets/icon.png", "demo/skills/hello/SKILL.md"):
            with self.subTest(kept=kept):
                self.assertIn(kept, names)
        self.assertFalse(any(n.startswith("demo/tests/") for n in names), names)

    def test_packages_the_committed_tree_not_the_working_tree(self):
        """A bump that is not committed yet must not rename the zip, or the zip
        would claim a version its contents do not have."""
        write_plugin(self.repo, "demo", "9.9.9")
        (self.repo / "plugins/demo/skills/uncommitted").mkdir()
        (self.repo / "plugins/demo/skills/uncommitted/SKILL.md").write_text("x")
        [path] = release.package(self.repo, self.dist)
        self.assertEqual(path.name, "demo-1.0.0.zip")
        with zipfile.ZipFile(path) as zf:
            self.assertEqual(json.loads(zf.read("demo/plugin.json"))["version"], "1.0.0")
            self.assertNotIn("demo/skills/uncommitted/SKILL.md", zf.namelist())

    def test_cli_warns_when_plugins_have_uncommitted_changes(self):
        write_plugin(self.repo, "demo", "9.9.9")
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = release.main(["--repo", str(self.repo), "--dist", str(self.dist), "package"])
        self.assertEqual(code, 0)
        self.assertIn("uncommitted changes under plugins/ are not packaged", err.getvalue())

    def test_removes_stale_zips_but_keeps_the_release_plan(self):
        self.dist.mkdir()
        (self.dist / "demo-0.9.0.zip").write_text("old")
        (self.dist / "release-plan").write_text("demo 1.0.0\n")
        release.package(self.repo, self.dist)
        self.assertFalse((self.dist / "demo-0.9.0.zip").exists())
        self.assertTrue((self.dist / "release-plan").exists())

    def test_zip_entries_carry_the_commit_time_so_rebuilds_match(self):
        """Stamped with the build time, a rebuild of the same commit never matches the
        SHA-256 digest GitHub shows for the released zip."""
        [path] = release.package(self.repo, self.dist)
        with zipfile.ZipFile(path) as zf:
            years = {info.date_time[0] for info in zf.infolist()}
        self.assertEqual(years, {2001})

    def test_a_commit_outside_the_plugin_does_not_change_the_zip(self):
        """The zip a reviewer uploads to OpenAI is built on the PR; the released one is
        built after the merge, on a different commit. Both must be the same bytes."""
        [before] = release.package(self.repo, self.dist)
        uploaded = before.read_bytes()
        (self.repo / "README.md").write_text("docs")
        commit(self.repo, "docs", date="2005-03-01T09:00:00Z")
        [after] = release.package(self.repo, self.dist)
        self.assertEqual(after.read_bytes(), uploaded)

    def test_the_zip_built_on_develop_matches_the_one_built_after_merging_to_main(self):
        run_git(self.repo, "switch", "-q", "-c", "develop")
        write_plugin(self.repo, "demo", "1.1.0")
        commit(self.repo, "bump", date="2003-01-01T00:00:00Z")
        [on_develop] = release.package(self.repo, self.dist)
        uploaded = on_develop.read_bytes()
        run_git(self.repo, "switch", "-q", "main")
        subprocess.run(
            ["git", "-C", str(self.repo), "merge", "-q", "--no-ff", "-m", "release", "develop"],
            check=True, env={**GIT_ENV, "GIT_COMMITTER_DATE": "2004-01-01T00:00:00Z"},
        )
        [on_main] = release.package(self.repo, self.dist)
        self.assertEqual(on_main.read_bytes(), uploaded)

    def test_writes_only_the_zips(self):
        """GitHub computes and shows a SHA-256 for every release asset, so no checksum
        file is published alongside the zips."""
        release.package(self.repo, self.dist)
        self.assertEqual({p.name for p in self.dist.iterdir()}, {"demo-1.0.0.zip"})

    def test_ignores_files_directly_under_plugins(self):
        (self.repo / "plugins/README.md").write_text("notes")
        commit(self.repo)
        self.assertEqual([p.name for p in release.discover_plugins(self.repo)], ["demo"])

    def test_rejects_a_plugin_folder_without_a_root_manifest(self):
        """Skipped silently, such a plugin would never be packaged or version-checked."""
        (self.repo / "plugins/claude-only/.claude-plugin").mkdir(parents=True)
        (self.repo / "plugins/claude-only/.claude-plugin/plugin.json").write_text("{}")
        commit(self.repo)
        with self.assertRaisesRegex(release.ReleaseError, "plugins/claude-only/ has no plugin.json"):
            release.discover_plugins(self.repo)

    def test_rejects_a_manifest_without_a_version(self):
        (self.repo / "plugins/demo/plugin.json").write_text(json.dumps({"name": "demo"}))
        commit(self.repo)
        with self.assertRaisesRegex(release.ReleaseError, "plugins/demo/plugin.json"):
            release.discover_plugins(self.repo)

    def test_rejects_a_version_that_cannot_be_a_tag(self):
        write_plugin(self.repo, "demo", "latest")
        commit(self.repo)
        with self.assertRaisesRegex(release.ReleaseError, "not a semantic version"):
            release.discover_plugins(self.repo)


def make_zip(path: Path, entries: dict[str, str], symlinks: tuple[str, ...] = ()) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in entries.items():
            zf.writestr(name, text)
        for name in symlinks:
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o120777 << 16  # S_IFLNK, as git archive writes links
            zf.writestr(info, "target")
    return path


VALID_ENTRIES = {
    "demo/plugin.json": json.dumps({"name": "demo", "version": "1.0.0",
                                    "extensions": {"logo": "./assets/icon.png"}}),
    "demo/assets/icon.png": "png",
    "demo/skills/hello/SKILL.md": "# hello",
}


class CheckZipTests(GitRepoTestCase):
    def check(self, entries: dict[str, str], symlinks: tuple[str, ...] = ()) -> list[str]:
        return release.check_zip(make_zip(self.tmp / "t.zip", entries, symlinks), "demo")

    def test_a_built_zip_passes(self):
        self.dist.mkdir()
        [plugin] = release.discover_plugins(self.repo)
        self.assertEqual(release.check_zip(release.build_zip(self.repo, plugin, self.dist), "demo"), [])

    def test_a_valid_handmade_zip_passes(self):
        self.assertEqual(self.check(VALID_ENTRIES), [])

    def test_rejects_entries_beside_the_plugin_root(self):
        problems = self.check({**VALID_ENTRIES, "README.txt": "x"})
        self.assertTrue(any("beside demo/" in p and "README.txt" in p for p in problems), problems)

    def test_rejects_a_missing_manifest(self):
        entries = {k: v for k, v in VALID_ENTRIES.items() if k != "demo/plugin.json"}
        self.assertIn("demo/plugin.json is missing", self.check(entries))

    def test_rejects_a_referenced_asset_that_is_not_packaged(self):
        entries = {k: v for k, v in VALID_ENTRIES.items() if k != "demo/assets/icon.png"}
        problems = self.check(entries)
        self.assertTrue(any("./assets/icon.png" in p for p in problems), problems)

    def test_rejects_packaged_tests(self):
        problems = self.check({**VALID_ENTRIES, "demo/tests/test_x.py": ""})
        self.assertTrue(any("demo/tests/" in p for p in problems), problems)

    def test_rejects_a_file_directly_under_skills_and_a_skill_without_skill_md(self):
        problems = self.check({**VALID_ENTRIES, "demo/skills/notes.md": "x",
                               "demo/skills/empty/README.md": "x"})
        self.assertTrue(any("demo/skills/notes.md" in p for p in problems), problems)
        self.assertTrue(any("demo/skills/empty" in p for p in problems), problems)

    def test_rejects_symlinks(self):
        problems = self.check(VALID_ENTRIES, symlinks=("demo/skills/hello/link",))
        self.assertTrue(any("symlink" in p and "demo/skills/hello/link" in p for p in problems), problems)

    def test_rejects_too_many_entries(self):
        with mock.patch.object(release, "MAX_ZIP_ENTRIES", 2):
            self.assertTrue(any("entries" in p for p in self.check(VALID_ENTRIES)))

    def test_rejects_an_oversized_zip(self):
        with mock.patch.object(release, "MAX_ZIP_BYTES", 10):
            self.assertTrue(any("MB" in p for p in self.check(VALID_ENTRIES)))


class PackageValidationTests(GitRepoTestCase):
    def test_package_fails_on_a_committed_stray_file_under_skills(self):
        (self.repo / "plugins/demo/skills/notes.md").write_text("x")
        commit(self.repo)
        with self.assertRaisesRegex(release.ReleaseError, "demo/skills/notes.md"):
            release.package(self.repo, self.dist)

    def test_package_fails_on_a_committed_symlink(self):
        os.symlink("SKILL.md", self.repo / "plugins/demo/skills/hello/link.md")
        commit(self.repo)
        with self.assertRaisesRegex(release.ReleaseError, "symlink"):
            release.package(self.repo, self.dist)



class ReleasePlanTests(GitRepoTestCase):
    def plan(self, repo: Path | None = None, **kwargs) -> list[str]:
        plugins = release.create_release_plan(repo or self.repo, self.dist, **kwargs)
        return [p.tag for p in plugins]

    def test_an_untagged_version_is_released(self):
        self.assertEqual(self.plan(), ["demo-v1.0.0"])
        self.assertEqual((self.dist / "release-plan").read_text(), "demo 1.0.0\n")

    def test_a_rerun_after_tagging_skips_the_plugin(self):
        """The release job tags HEAD; running it again must not fail or re-release."""
        run_git(self.repo, "tag", "demo-v1.0.0")
        self.assertEqual(self.plan(), [])
        self.assertEqual((self.dist / "release-plan").read_text(), "")

    def test_a_change_outside_plugins_releases_nothing(self):
        run_git(self.repo, "tag", "demo-v1.0.0")
        (self.repo / "README.md").write_text("docs")
        commit(self.repo)
        self.assertEqual(self.plan(), [])

    def test_a_plugin_change_without_a_bump_fails(self):
        run_git(self.repo, "tag", "demo-v1.0.0")
        (self.repo / "plugins/demo/skills/hello/SKILL.md").write_text("# changed")
        commit(self.repo)
        with self.assertRaisesRegex(
            release.ReleaseError,
            "demo changed since demo-v1.0.0 but version is still 1.0.0",
        ):
            self.plan()
        self.assertFalse((self.dist / "release-plan").exists())

    def test_a_plugin_change_with_a_bump_is_released(self):
        run_git(self.repo, "tag", "demo-v1.0.0")
        write_plugin(self.repo, "demo", "1.1.0")
        commit(self.repo)
        self.assertEqual(self.plan(), ["demo-v1.1.0"])

    def test_every_failing_plugin_is_reported(self):
        write_plugin(self.repo, "other", "2.0.0")
        commit(self.repo)
        run_git(self.repo, "tag", "demo-v1.0.0")
        run_git(self.repo, "tag", "other-v2.0.0")
        for name in ("demo", "other"):
            (self.repo / f"plugins/{name}/mcp.json").write_text('{"changed": true}')
        commit(self.repo)
        with self.assertRaises(release.ReleaseError) as caught:
            self.plan()
        self.assertIn("demo changed", str(caught.exception))
        self.assertIn("other changed", str(caught.exception))

    def test_a_failing_git_command_is_reported_with_its_message(self):
        """No origin to fetch from: the error has to say so, not end in a traceback."""
        with self.assertRaisesRegex(release.ReleaseError, "git fetch .*origin"):
            self.plan(fetch=True)

    def test_fetch_sees_a_tag_that_only_exists_on_origin(self):
        """A stale clone must not pass a check that CI, which has every tag, fails."""
        origin = self.tmp / "origin.git"
        run_git(self.tmp, "clone", "-q", "--bare", str(self.repo), str(origin))
        clone = self.tmp / "clone"
        run_git(self.tmp, "clone", "-q", str(origin), str(clone))
        run_git(self.repo, "remote", "add", "origin", str(origin))
        run_git(self.repo, "tag", "demo-v1.0.0")
        run_git(self.repo, "push", "-q", "origin", "demo-v1.0.0")
        self.assertEqual(self.plan(clone), ["demo-v1.0.0"])
        self.assertEqual(self.plan(clone, fetch=True), [])

    def test_cli_prints_the_plan_and_fails_with_a_message(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = release.main(["--repo", str(self.repo), "--dist", str(self.dist), "release-plan"])
        self.assertEqual((code, out.getvalue()), (0, "release demo-v1.0.0\n"))

        run_git(self.repo, "tag", "demo-v1.0.0")
        (self.repo / "plugins/demo/mcp.json").write_text('{"changed": true}')
        commit(self.repo)
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = release.main(["--repo", str(self.repo), "--dist", str(self.dist), "release-plan"])
        self.assertEqual(code, 1)
        self.assertIn("bump version in both plugin.json files", err.getvalue())


if __name__ == "__main__":
    unittest.main()
