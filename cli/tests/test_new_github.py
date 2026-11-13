# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`ohmai new --github` never calls a real `gh` in tests."""
from __future__ import annotations

import subprocess

import pytest

from ohmai.cli import main
from ohmai.newproject import (
    GithubError,
    github_create_argv,
    parse_github_repo,
    publish_github,
)


def test_parse_github_repo_accepts_owner_name():
    assert parse_github_repo("ohmai-pcb/demo") == "ohmai-pcb/demo"
    assert parse_github_repo("  A/b  ") == "A/b"


@pytest.mark.parametrize("bad", ["", "norg", "a/b/c", "../x", "a/b.git/", " owner / name ", "/n", "n/", "a/."])
def test_parse_github_repo_rejects_bad_specs(bad):
    with pytest.raises(GithubError, match="invalid --github"):
        parse_github_repo(bad)


def test_github_create_argv_defaults_private(tmp_path):
    dest = tmp_path / "proj"
    dest.mkdir()
    assert github_create_argv(dest, "org/name", True) == [
        "gh", "repo", "create", "org/name", "--source", str(dest.resolve()), "--private", "--push",
    ]
    assert "--public" in github_create_argv(dest, "org/name", False)
    assert "--private" not in github_create_argv(dest, "org/name", False)


def test_publish_github_refuses_missing_gh(tmp_path):
    with pytest.raises(GithubError, match="gh is not installed"):
        publish_github(tmp_path, "org/name", yes=True, which=lambda _=None: None, run=lambda *a, **k: pytest.fail("ran"))


def test_publish_github_refuses_unauthenticated(tmp_path):
    def run(argv, **kwargs):
        assert argv == ["gh", "auth", "status"]
        return subprocess.CompletedProcess(argv, 1, "", "not logged in")

    with pytest.raises(GithubError, match="not authenticated"):
        publish_github(tmp_path, "org/name", yes=True, which=lambda _=None: "/usr/bin/gh", run=run)


def test_publish_github_yes_runs_exact_create_command(tmp_path):
    dest = tmp_path / "board"
    dest.mkdir()
    seen: list[list[str]] = []

    def run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "https://github.com/org/board\n", "")

    log = publish_github(
        dest, "org/board", private=True, yes=True,
        which=lambda _=None: "/usr/bin/gh", run=run,
    )
    assert seen[0] == ["gh", "auth", "status"]
    assert seen[1] == github_create_argv(dest, "org/board", True)
    assert any("created org/board and pushed" in line for line in log)
    assert not any("gh repo create" in line and "org/other" in line for line in log)


def test_publish_github_public_flag_and_cancel(tmp_path):
    dest = tmp_path / "pub"
    dest.mkdir()
    seen: list[list[str]] = []

    def run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    with pytest.raises(GithubError, match="cancelled"):
        publish_github(
            dest, "org/pub", private=False, yes=False,
            which=lambda _=None: "/usr/bin/gh", run=run, confirm=lambda _prompt: False,
        )
    assert seen == [["gh", "auth", "status"]]  # must not run repo create


def test_publish_github_create_failure(tmp_path):
    dest = tmp_path / "x"
    dest.mkdir()

    def run(argv, **kwargs):
        if argv[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "Name already exists")

    with pytest.raises(GithubError, match="already exists"):
        publish_github(dest, "org/x", yes=True, which=lambda _=None: "/usr/bin/gh", run=run)



def test_publish_github_unattended_without_yes(tmp_path):
    class _NoTty:
        def isatty(self):
            return False

    def run(argv, **kwargs):
        if argv[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command {argv}")

    with pytest.raises(GithubError, match="unattended"):
        publish_github(
            tmp_path, "org/n", yes=False,
            which=lambda _=None: "/usr/bin/gh", run=run, stdin=_NoTty(),
        )

def test_cli_github_requires_git_and_flag_combo(tmp_path, capsys):
    d = tmp_path / "n"
    assert main(["new", str(d), "--idea", "x", "--no-git", "--github", "org/n"]) == 2
    assert "--github cannot be used with --no-git" in capsys.readouterr().err
    assert main(["new", str(tmp_path / "p"), "--idea", "x", "--no-git", "--yes"]) == 2
    assert "--yes require --github" in capsys.readouterr().err


def test_cli_github_yes_uses_injected_publish(tmp_path, monkeypatch, capsys):
    """End-to-end through main(), with publish_github mocked so gh is never started."""
    called: dict = {}

    def fake_publish(dest, repo, *, private=True, yes=False, **kwargs):
        called.update(dest=dest, repo=repo, private=private, yes=yes)
        return [f"GitHub: created {repo} and pushed"]

    # cmd_new imports publish_github at call time from ohmai.newproject.
    monkeypatch.setattr("ohmai.newproject.publish_github", fake_publish)
    d = tmp_path / "g"
    assert main(["new", str(d), "--idea", "A board", "--github", "org/g", "--yes"]) == 0
    out = capsys.readouterr().out
    assert called["repo"] == "org/g"
    assert called["private"] is True
    assert called["yes"] is True
    assert called["dest"] == d.resolve()
    assert "created org/g and pushed" in out
    assert (d / "STATUS.md").is_file()


def test_cli_github_public(tmp_path, monkeypatch, capsys):
    called: dict = {}

    def fake_publish(dest, repo, *, private=True, yes=False, **kwargs):
        called.update(private=private, repo=repo, yes=yes)
        return ["ok"]

    monkeypatch.setattr("ohmai.newproject.publish_github", fake_publish)
    d = tmp_path / "pub"
    assert main(["new", str(d), "--idea", "A board", "--github", "org/pub", "--public", "--yes"]) == 0
    assert called == {"private": False, "repo": "org/pub", "yes": True}
