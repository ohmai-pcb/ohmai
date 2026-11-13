# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`ohmai new`: create a project repo from the template."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import uuid
from datetime import date
from pathlib import Path

from ohmai import __version__
from ohmai.resources import skill_dir, template_dir

TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".csv", ".json", ".kicad_pro", ".kicad_sch", ".kicad_pcb", ".txt",
                 ".gitignore", ".gitattributes"}


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s or "project"


def _git(dest: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(dest), *args], capture_output=True, text=True, check=check)


def create(dest: Path, idea: str, name: str | None = None, git: bool = True, anonymous: bool = False) -> list[str]:
    """Create the project; returns log lines. Raises on fatal problems."""
    dest = dest.resolve()
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} exists and is not empty")
    name = name or dest.name
    slug = slugify(name)
    tpl = template_dir()
    shutil.copytree(tpl, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "out"))
    log = [f"copied template from {tpl}"]

    hw = dest / "hardware"
    for f in hw.glob("ohmai_project.*"):
        f.rename(hw / f"{slug}{f.suffix}")
    log.append(f"hardware/{slug}.kicad_pro/.kicad_sch/.kicad_pcb")

    phases_src = skill_dir() / "phases"
    if phases_src.is_dir():
        shutil.copytree(phases_src, dest / ".ohmai" / "phases")
        log.append(f".ohmai/phases/ pinned from OhmAI {__version__}")

    subs = {
        "{{project_name}}": name,
        "{{project_slug}}": slug,
        "{{idea}}": idea.strip(),
        "{{ohmai_version}}": __version__,
        "{{date}}": date.today().isoformat(),
        "{{uuid}}": str(uuid.uuid4()),
    }
    for f in dest.rglob("*"):
        if f.is_file() and (f.suffix in TEXT_SUFFIXES or f.name in TEXT_SUFFIXES) and ".git" not in f.parts:
            text = f.read_text(encoding="utf-8")
            new = text
            for k, v in subs.items():
                new = new.replace(k, v)
            if new != text:
                f.write_text(new, encoding="utf-8")

    if anonymous:
        cfg_file = dest / "ohmai.yaml"
        if cfg_file.is_file():
            cfg_file.write_text(
                cfg_file.read_text(encoding="utf-8").replace("\nanonymous: false\n", "\nanonymous: true\n", 1),
                encoding="utf-8",
            )
            log.append("ohmai.yaml: anonymous: true enabled")

    if git:
        if not shutil.which("git"):
            log.append("git not found: skipped git init")
            return log
        _git(dest, "init", "-q", "-b", "main")
        if shutil.which("git-lfs"):
            _git(dest, "lfs", "install", "--local", "--skip-repo", check=False)
            log.append("git lfs enabled (datasheets/)")
        else:
            log.append("git-lfs not installed: datasheets/ will be stored as normal blobs until it is")
        _git(dest, "add", "-A")
        ident = []
        if _git(dest, "config", "user.email", check=False).returncode != 0:
            ident = ["-c", "user.name=ohmai-bot", "-c", "user.email=ohmai@example.invalid"]
            log.append("no git identity configured: initial commit authored as ohmai-bot <ohmai@example.invalid>")
        from ohmai import trailers as tr

        trailer = tr.format_trailer({"name": "ohmai new", "kind": "agent", "vendor": "OhmAI",
                                     "model": "none (template copy)", "instructions": f"ohmai {__version__}"})
        subprocess.run(["git", "-C", str(dest), *ident, "commit", "-q", "-m",
                        f"Create {name} from OhmAI {__version__} template", "-m", f"{tr.KEY}: {trailer}"],
                       check=True)
        log.append(f"git: commit-msg hook {tr.install_hook(dest)} (commits need an {tr.KEY} trailer)")
        _git(dest, "checkout", "-q", "-b", "phase/requirements")
        log.append("git: initial commit on main; now on branch phase/requirements")
    return log


GITHUB_REPO_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?)/"
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?)$"
)


class GithubError(Exception):
    """User-facing error from `ohmai new --github`."""


def parse_github_repo(spec: str) -> str:
    """Return a validated OWNER/NAME. Raises GithubError on a bad spec."""
    spec = (spec or "").strip()
    m = GITHUB_REPO_RE.fullmatch(spec)
    if not m:
        raise GithubError(
            f"invalid --github value {spec!r}; expected OWNER/NAME "
            "(letters, digits, hyphen, underscore or dot; no leading or trailing punctuation)"
        )
    return f"{m.group('owner')}/{m.group('name')}"


def github_create_argv(dest: Path, repo: str, private: bool) -> list[str]:
    visibility = "--private" if private else "--public"
    return ["gh", "repo", "create", repo, "--source", str(dest.resolve()), visibility, "--push"]


def ensure_gh_ready(*, which=shutil.which, run=subprocess.run) -> None:
    if not which("gh"):
        raise GithubError(
            "gh is not installed or not on PATH. Install the GitHub CLI "
            "(https://cli.github.com/) and run `gh auth login`."
        )
    result = run(["gh", "auth", "status"], capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        extra = f" ({detail})" if detail else ""
        raise GithubError(
            "GitHub CLI is not authenticated. Run `gh auth login` and retry." + extra
        )


def _confirm_github(prompt: str, *, yes: bool, confirm=None, stdin=None) -> bool:
    if yes:
        return True
    if confirm is not None:
        return bool(confirm(prompt))
    stream = stdin if stdin is not None else sys.stdin
    if not hasattr(stream, "isatty") or not stream.isatty():
        raise GithubError(
            "--github needs an interactive y/N confirm or --yes "
            "(refusing to run `gh repo create` unattended)"
        )
    try:
        answer = input(prompt)
    except EOFError as e:
        raise GithubError(
            "--github needs an interactive y/N confirm or --yes "
            "(refusing to run `gh repo create` unattended)"
        ) from e
    return answer.strip().lower() in {"y", "yes"}


def publish_github(
    dest: Path,
    repo: str,
    *,
    private: bool = True,
    yes: bool = False,
    which=shutil.which,
    run=subprocess.run,
    confirm=None,
    stdin=None,
) -> list[str]:
    """Print the exact `gh repo create` command, confirm, then run it.

    Never called with a live `run` from tests. Default visibility is private.
    """
    dest = dest.resolve()
    repo = parse_github_repo(repo)
    ensure_gh_ready(which=which, run=run)
    argv = github_create_argv(dest, repo, private)
    visibility = "private" if private else "public"
    log = [
        f"GitHub: will create {repo} as {visibility} from {dest} and push",
        "command: " + " ".join(argv),
    ]
    prompt = (
        f"Create GitHub repository {repo} ({visibility}) and push {dest}? [y/N] "
    )
    if not _confirm_github(prompt, yes=yes, confirm=confirm, stdin=stdin):
        raise GithubError(
            "cancelled; the local project was created, but no GitHub repository was made"
        )
    result = run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
        raise GithubError(f"`gh repo create` failed: {detail}")
    if result.stdout.strip():
        log.append(result.stdout.strip())
    log.append(f"GitHub: created {repo} and pushed")
    return log
