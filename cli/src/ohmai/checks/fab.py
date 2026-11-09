# SPDX-License-Identifier: Apache-2.0
"""Phase 11: fab outputs can be generated (preview in out/fab-preview, never committed)."""

from __future__ import annotations

from ohmai import kicad
from ohmai.checks import FAIL, PASS, SKIP, Result, register
from ohmai.project import Project


@register("fab-outputs", "Gerbers, drill, PnP, PDFs export cleanly with kicad-cli (preview in out/fab-preview)")
def fab_outputs(project: Project, opts: dict) -> Result:
    from ohmai.release import export_fab

    if not kicad.kicad_cli():
        return Result("fab-outputs", SKIP, "kicad-cli not found")
    if not project.pcb.is_file():
        return Result("fab-outputs", FAIL, f"{project.pcb} not found")
    problems = export_fab(project, project.out / "fab-preview")
    if problems:
        return Result("fab-outputs", FAIL, f"{len(problems)} export problem(s)", problems)
    return Result("fab-outputs", PASS, "outputs written to out/fab-preview/")
