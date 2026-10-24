"""`ohmai doctor`: environment self-check (works in a project and in any git repo, e.g. the OhmAI repo itself)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Item:
    name: str
    status: str   # PASS / WARN / FAIL / INFO
    message: str


def run(cwd: Path) -> list[Item]:
    from ohmai import kicad
    from ohmai import trailers as tr
    from ohmai.project import NotAProject, find_project

    out: list[Item] = []
    try:
        proj = find_project(cwd)
        out.append(Item("project", "INFO", f"{proj.root}"))
        root: Path | None = proj.root
    except NotAProject:
        out.append(Item("project", "INFO", "not inside an OhmAI project (checking the git repo only)"))
        r = tr._git(cwd, "rev-parse", "--show-toplevel")
        root = Path(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    if root is None or not tr.is_repo(root):
        out.append(Item("git", "WARN", "not a git repository: commits and their trailers cannot be checked"))
    else:
        out.append(Item("git", "PASS", f"repo at {root}"))
        hp = tr._git(root, "config", "--get", "core.hooksPath").stdout.strip()
        ok, msg = tr.hook_status(root)
        if hp:
            msg += f" (core.hooksPath = {hp})"
        if ok:
            out.append(Item("hooks", "PASS", msg))
        elif os.environ.get("CI"):
            out.append(Item("hooks", "INFO", msg + "; CI clone, hooks are not versioned"))
        else:
            out.append(Item("hooks", "WARN", msg + ": commits without an OhmAI-Author trailer are not rejected"))
    cli = kicad.kicad_cli()
    out.append(Item("kicad-cli", "PASS" if cli else "WARN", str(cli) if cli else
                    "not found: DRC/ERC/netlist checks are skipped"))
    return out
