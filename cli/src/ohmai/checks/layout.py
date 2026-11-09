# SPDX-License-Identifier: Apache-2.0
"""Layout checks: DRC with schematic parity."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path

from ohmai import kicad
from ohmai.checks import FAIL, PASS, SKIP, TODO, WARN, Result, register
from ohmai.project import Project


@register("drc", "kicad-cli DRC with schematic parity, JSON report in out/drc.json")
def drc(project: Project, opts: dict) -> Result:
    if not kicad.kicad_cli():
        return Result("drc", SKIP, "kicad-cli not found")
    if not project.pcb.is_file():
        return Result("drc", FAIL, f"{project.pcb} not found")
    out = project.out / "drc.json"
    # Drop any prior report so a failed load cannot reuse stale counts.
    if out.is_file():
        out.unlink()
    # Run from the PCB directory so .kicad_pro / .kicad_dru next to the board (or a staged
    # copy that received _ensure_staged_project_files) are applied — not default rules.
    r = kicad.run(["pcb", "drc", "--format", "json", "--severity-all", "--schematic-parity", "-o", str(out),
                   str(project.pcb)], log=project.out / "drc.log", cwd=project.pcb.parent)
    if kicad.load_failed(r):
        return Result("drc", FAIL, "Failed to load board (see out/drc.log)")
    try:
        d = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return Result("drc", FAIL, f"no DRC report ({e}); see out/drc.log")
    items = (
        [("violation", v) for v in d.get("violations", [])]
        + [("unconnected", v) for v in d.get("unconnected_items", [])]
        + [("parity", v) for v in d.get("schematic_parity", [])]
    )
    sev = Counter(v.get("severity", "?") for _, v in items)
    kinds = Counter(k for k, _ in items)
    details = [f"[{v.get('severity')}] {k} {v.get('type')}: {v.get('description', '')}" for k, v in items[:50]]
    summary = f"{len(items)} item(s): " + ", ".join(f"{n} {k}" for k, n in kinds.items()) if items else "clean"
    failing = sev.get("error", 0) + sev.get("warning", 0)
    return Result("drc", FAIL if failing else PASS, summary, details)


@register("creepage", "Creepage/clearance between net classes (TODO: script; KiCad 9 has DRC creepage rules)")
def creepage(project: Project, opts: dict) -> Result:
    # TODO(v0.3): read safety.md clearances table -> generate .kicad_dru creepage rules, then rely on DRC.
    return Result("creepage", TODO, "not implemented; encode clearances as custom DRC rules (.kicad_dru)")


# ---------- footprints: every part has a resolvable footprint whose pads match the symbol pins ----------

_PAD = re.compile(r'\(pad\s+"([^"]*)"\s+(\w+)')
_LIB = re.compile(r'\(lib\s+\(name\s+"([^"]+)"\)\s*\(type\s+"[^"]*"\)\s*\(uri\s+"([^"]+)"\)')
_PAGE = re.compile(r"\bp\.\s?\d+")


def fp_lib_dirs(project: Project) -> dict[str, Path]:
    """Nickname -> .pretty dir from the project fp-lib-table (${KIPRJMOD} and env vars expanded)."""
    tbl = project.schematic.parent / "fp-lib-table"
    out: dict[str, Path] = {}
    if not tbl.is_file():
        return out
    for name, uri in _LIB.findall(tbl.read_text(encoding="utf-8")):
        uri = uri.replace("${KIPRJMOD}", str(project.schematic.parent))
        out[name] = Path(os.path.expandvars(uri))
    return out


def footprint_pads(path: Path) -> tuple[set[str], str]:
    """Numbered pads (electrical, i.e. not unnamed paste/mechanical) and the descr text of a .kicad_mod."""
    s = path.read_text(encoding="utf-8")
    pads = {n for n, kind in _PAD.findall(s) if n and kind != "np_thru_hole"}
    m = re.search(r'\(descr\s+"((?:[^"\\]|\\.)*)"', s)
    return pads, (m.group(1) if m else "")


def _netlist_footprints(path: Path) -> dict[str, str]:
    from ohmai.sexp import find, first, parse

    root = parse(Path(path).read_text(encoding="utf-8"))
    out = {}
    for c in find(first(root, "components") or [], "comp"):
        ref = first(c, "ref")
        fp = first(c, "footprint")
        out[ref[1] if ref else "?"] = fp[1] if fp and len(fp) > 1 else ""
    return out


@register("footprints", "Every part has a footprint that resolves in the project fp-lib-table; every symbol pin is a "
                        "pad and every pad a symbol pin; project-library footprints cite a datasheet page (descr)")
def footprints(project: Project, opts: dict) -> Result:
    from ohmai.checks.schematic import export_netlist
    from ohmai.netlist import load as load_netlist

    try:
        net = export_netlist(project, opts)
    except (RuntimeError, FileNotFoundError) as e:
        return Result("footprints", SKIP if "kicad-cli" in str(e) else FAIL, str(e))
    nl = load_netlist(net)
    fps = _netlist_footprints(net)
    libs = fp_lib_dirs(project)
    proj_lib = (project.schematic.parent).resolve()
    errs, warns = [], []
    seen = 0
    for ref in sorted(nl.comps, key=lambda r: (re.sub(r"\d", "", r), int(re.sub(r"\D", "", r) or 0))):
        fp = fps.get(ref, "")
        if not fp:
            errs.append(f"{ref}: no footprint")
            continue
        lib, _, name = fp.partition(":")
        d = libs.get(lib)
        f = d / f"{name}.kicad_mod" if d else None
        if f is None or not f.is_file():
            errs.append(f"{ref}: footprint {fp} not found (fp-lib-table nickname '{lib}')")
            continue
        seen += 1
        pads, descr = footprint_pads(f)
        pins = set(nl.pins_of(ref))
        if not pins:
            continue
        missing = sorted(pins - pads)
        extra = sorted(pads - pins)
        if missing:
            errs.append(f"{ref} ({fp}): symbol pin(s) without a pad: {', '.join(missing)}")
        if extra:
            warns.append(f"{ref} ({fp}): pad(s) not on the symbol (unconnected copper): {', '.join(extra)}")
        try:
            in_project = d.resolve().is_relative_to(proj_lib)
        except AttributeError:   # pragma: no cover
            in_project = str(d.resolve()).startswith(str(proj_lib))
        if in_project and not _PAGE.search(descr):
            errs.append(f"{ref} ({fp}): project footprint without a datasheet page cite in descr")
    st = FAIL if errs else (WARN if warns else PASS)
    summary = f"{seen} footprint(s) resolved; {len(errs)} error(s), {len(warns)} warning(s)"
    return Result("footprints", st, summary, errs + warns)
