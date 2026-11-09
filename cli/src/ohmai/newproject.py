# SPDX-License-Identifier: Apache-2.0
"""`ohmai new`: create a project repo from the template."""

from __future__ import annotations

import re
import shutil
import subprocess
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
