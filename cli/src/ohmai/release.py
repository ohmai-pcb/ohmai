# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`ohmai release rev-X`: preflight, fab outputs via kicad-cli, manifest, optional local tag.

Never pushes. The GitHub release (gerbers/BOM/PnP) is made by CI (KiBot) when the tag is pushed,
or by hand with the `gh release create` command printed at the end.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

from ohmai import __version__, kicad
from ohmai.project import Project

REV_RE = re.compile(r"^rev-[A-Z][0-9]*$")
PREFLIGHT = ("erc", "drc", "bom-lock", "docs:docs/tool-issues.md")


def _git(project: Project, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(project.root), *args], capture_output=True, text=True)


def export_fab(project: Project, dest: Path) -> list[str]:
    """Gerbers, drill, PnP, schematic PDF, BOM copy into dest. Returns problems (empty = ok)."""
    problems = []
    shutil.rmtree(dest, ignore_errors=True)
    (dest / "gerbers").mkdir(parents=True)
    logs = dest / "logs"
    pcb, sch = str(project.pcb), str(project.schematic)
    steps = [
        ("gerbers", ["pcb", "export", "gerbers", "-o", str(dest / "gerbers") + "/", pcb]),
        ("drill", ["pcb", "export", "drill", "--generate-map", "-o", str(dest / "gerbers") + "/", pcb]),
        ("pnp", ["pcb", "export", "pos", "--format", "csv", "--units", "mm", "-o", str(dest / "pnp.csv"), pcb]),
        ("schematic-pdf", ["sch", "export", "pdf", "-o", str(dest / "schematic.pdf"), sch]),
        ("assembly-pdf", ["pcb", "export", "pdf", "--layers", "F.Fab,Edge.Cuts", "-o",
                          str(dest / "assembly-top.pdf"), pcb]),
    ]
    for name, args in steps:
        r = kicad.run(args, log=logs / f"{name}.log")
        if r.returncode != 0:
            problems.append(f"{name}: kicad-cli exit {r.returncode} (see {logs / (name + '.log')})")
    # TODO(v0.4): assembly drawings with refdes/pin-1 marks and STEP via KiBot (AGPL, run as a separate process).
    if project.bom.is_file():
        shutil.copy2(project.bom, dest / "bom.csv")
    else:
        problems.append("bom/bom.csv missing")
    if not any((dest / "gerbers").iterdir()):
        problems.append("no gerber files written")
    else:
        shutil.make_archive(str(dest / "gerbers"), "zip", dest / "gerbers")
    return problems


def release(project: Project, rev: str, dry_run: bool, tag: bool, force: bool) -> int:
    from ohmai.checks import FAIL, run

    if not REV_RE.match(rev):
        print(f"release: '{rev}' is not a revision tag (use rev-A, rev-B, ...)")
        return 2
    print(f"release {rev} (OhmAI {__version__}, {'dry run' if dry_run else 'build'})")
    problems = []
    if _git(project, "status", "--porcelain").stdout.strip():
        problems.append("working tree not clean: commit or stash first")
    branch = _git(project, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        problems.append(f"on branch '{branch}': releases are cut from main (the approved state)")
    if _git(project, "rev-parse", "-q", "--verify", f"refs/tags/{rev}").returncode == 0:
        problems.append(f"tag {rev} already exists")
    if not kicad.kicad_cli():
        problems.append("kicad-cli not found")
    for name in PREFLIGHT:
        r = run(project, name, {})
        print(f"  {r.status:5} {name:10} {r.summary}")
        if r.status == FAIL:
            problems.append(f"{name} fails")
    for p in problems:
        print(f"  BLOCK {p}")
    if problems and not (force or dry_run):
        print("release: blocked (fix the items above; --dry-run to preview outputs anyway)")
        return 1
    dest = project.out / "release" / rev
    if kicad.kicad_cli():
        export_problems = export_fab(project, dest)
        for p in export_problems:
            print(f"  FAIL  export {p}")
        problems += export_problems
    sha = _git(project, "rev-parse", "--short", "HEAD").stdout.strip() or "unknown"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "MANIFEST.md").write_text(
        f"# {project.config.get('project', project.root.name)} {rev}\n\n"
        f"- Date: {date.today().isoformat()}\n- Commit: {sha}\n- OhmAI: {__version__}\n"
        f"- KiCad: {kicad.version() or 'n/a'}\n\n"
        "## Release checklist (owner ticks before ordering)\n\n"
        "- [ ] ERC/DRC clean with fab rules (`ohmai check fab`)\n"
        "- [ ] BOM locked; fab actual-purchase list diffed and approved (`ohmai check bom --fab ...`)\n"
        "- [ ] 3D view, 1:1 print, pin-1 and rotation checked against PnP\n"
        "- [ ] Bring-up plan written (`test/bringup-plan.md`)\n"
        "- [ ] All-tool issue report complete (`docs/tool-issues.md`), with versions, repros, impacts, workarounds, fix commits, severity and upstream drafts\n",
        encoding="utf-8",
    )
    print(f"  outputs in {dest.relative_to(project.root)}/ (not committed; attach to the GitHub release)")
    if problems and not force:
        print("release: dry run finished with problems" if dry_run else "release: FAILED")
        return 1
    if dry_run:
        print("release: dry run OK; rerun without --dry-run (add --tag to tag locally)")
        return 0
    if tag:
        r = _git(project, "tag", "-a", rev, "-m", f"{rev}: fab release (OhmAI {__version__})")
        if r.returncode != 0:
            print(f"release: git tag failed: {r.stderr.strip()}")
            return 1
        print(f"  tagged {rev} locally. Push when the owner approves: git push origin {rev}")
    print(f"  manual alternative to CI: gh release create {rev} {dest.relative_to(project.root)}/gerbers.zip "
          f"{dest.relative_to(project.root)}/bom.csv {dest.relative_to(project.root)}/pnp.csv "
          f"--title '{rev}' --notes-file {dest.relative_to(project.root)}/MANIFEST.md")
    return 0
