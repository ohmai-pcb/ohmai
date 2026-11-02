"""lib-refs: every symbol (lib_id) and footprint the schematic or BOM names must resolve through the
project's sym-lib-table / fp-lib-table. Missing libraries are a tool problem, not owner homework: import a
vetted part with `ohmai kicad-libs import` (manufacturer or Ultra Librarian export) into hardware/lib/,
which adds the ${KIPRJMOD} table row and hardware/lib/manifest.yaml (skill/phases/04-parts.md "Missing KiCad library parts").
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path

from ohmai.checks import FAIL, PASS, SKIP, WARN, Result, register
from ohmai.project import Project

NAME = "lib-refs"
NAME_STRICT = "lib-refs-strict"
_ROW = re.compile(r'\(lib\s+\(name\s+"?([^")\s]+)"?\)\s*\(type\s+"?[^")]*"?\)\s*\(uri\s+"?([^")]+)"?\)')
_VAR = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
_LIBID = re.compile(r'\(lib_id\s+"([^"]+)"\)')
_FPPROP = re.compile(r'\(property\s+"Footprint"\s+"([^"]+)"')
_SYMDEF = re.compile(r'^\s*\(symbol\s+"([^"]+)"', re.M)


def _table(path: Path, hw: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not path.is_file():
        return out
    env = dict(os.environ, KIPRJMOD=str(hw))
    for name, uri in _ROW.findall(path.read_text(encoding="utf-8", errors="replace")):
        out[name] = Path(_VAR.sub(lambda m: env.get(m.group(1), m.group(0)), uri))
    return out


def _sym_names(f: Path, cache: dict) -> set[str]:
    if f not in cache:
        txt = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
        cache[f] = {n for n in _SYMDEF.findall(txt) if not re.search(r"_\d+_\d+$", n)}
    return cache[f]


def refs(project: Project) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """(symbol refs, footprint refs) as (lib:item, where)."""
    from ohmai.checks.schematic import sheet_files

    syms: list[tuple[str, str]] = []
    fps: list[tuple[str, str]] = []
    if project.schematic.is_file():
        sheets, _ = sheet_files(project.schematic)
        for s in sheets:
            txt = s.read_text(encoding="utf-8", errors="replace")
            body = re.sub(r"\(lib_symbols\b.*?\n  \)\n", "", txt, flags=re.S)  # embedded cache is not a reference
            rel = s.name
            syms += [(x, rel) for x in _LIBID.findall(body)]
            fps += [(x, rel) for x in _FPPROP.findall(body) if x.partition(":")[2].strip()]
    bom = project.root / "bom" / "bom.csv"
    if bom.is_file():
        with bom.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                fp = (row.get("footprint") or "").strip()
                if fp.partition(":")[2].strip():
                    fps.append((fp, f"bom.csv {row.get('refs', '')}".strip()))
    return syms, fps


@register(NAME, "Every schematic lib_id resolves through hardware/sym-lib-table (FAIL); schematic/BOM footprints "
                "through fp-lib-table (WARN until layout). Missing part -> import a vetted one into hardware/lib/")
def lib_refs(project: Project, opts: dict) -> Result:
    return _check(project, NAME, strict=False)


@register(NAME_STRICT, "lib-refs with missing footprints as FAIL too (layout and BOM lock)")
def lib_refs_strict(project: Project, opts: dict) -> Result:
    return _check(project, NAME_STRICT, strict=True)


def _check(project: Project, name: str, strict: bool) -> Result:
    from ohmai import kicadlibs

    syms, fps = refs(project)
    if not syms and not fps:
        return Result(name, SKIP, "no schematic symbols or BOM footprints yet")
    tag = kicadlibs.pinned(project)
    if tag and not all(d.is_dir() for d in kicadlibs.dirs(tag)):
        return Result(name, SKIP, f"pinned KiCad libraries {tag} not installed (ohmai kicad-libs install)")
    hw = project.schematic.parent
    st, ft = kicadlibs.table_paths(project)
    stab, ftab = _table(st, hw), _table(ft, hw)
    problems: list[str] = []
    fp_problems: list[str] = []
    cache: dict = {}
    seen = set()
    emb = embedded_policy(project)
    embedded_ok: list[str] = []
    for ref, where in syms:
        if (ref, "s") in seen:
            continue
        seen.add((ref, "s"))
        lib, _, item = ref.partition(":")
        if emb is not None and (lib not in stab or not stab[lib].is_file() or item not in _sym_names(stab[lib], cache)):
            why = emb.get(ref) or emb.get(item)
            if why:
                embedded_ok.append(f"symbol {ref} ({where}): embedded, verified by pin-map against {why}")
                continue
            problems.append(f"symbol {ref} ({where}): embedded symbol with no constraints file "
                            "(match.lib_symbol) to verify its pins; add parts/constraints/<MPN>.yaml so pin-map "
                            "checks it against the datasheet, or import a vetted symbol")
            continue
        if lib not in stab:
            problems.append(f"symbol {ref} ({where}): library '{lib}' not in sym-lib-table")
        elif not stab[lib].is_file():
            problems.append(f"symbol {ref} ({where}): {stab[lib]} missing")
        elif item not in _sym_names(stab[lib], cache):
            problems.append(f"symbol {ref} ({where}): '{item}' not defined in {stab[lib].name}")
    for ref, where in fps:
        if (ref, "f") in seen:
            continue
        seen.add((ref, "f"))
        lib, _, item = ref.partition(":")
        if lib not in ftab:
            fp_problems.append(f"footprint {ref} ({where}): library '{lib}' not in fp-lib-table")
        elif not (ftab[lib] / f"{item}.kicad_mod").is_file():
            fp_problems.append(f"footprint {ref} ({where}): {item}.kicad_mod not in {ftab[lib]}")
    n = len({r for r, _ in syms}) + len({r for r, _ in fps})
    hint = "(run `ohmai kicad-libs fetch|import <MPN>`, skill phase 04 'Missing KiCad library parts')"
    if problems or (strict and fp_problems):
        bad = problems + fp_problems
        return Result(name, FAIL, f"{len(bad)} of {n} library reference(s) unresolved {hint}", bad)
    if fp_problems:
        return Result(name, WARN, f"symbols resolve; {len(fp_problems)} footprint reference(s) unresolved "
                      f"(FAIL from layout / BOM lock) {hint}", fp_problems)
    if embedded_ok:
        return Result(name, PASS, f"{n} reference(s) resolve; {len(embedded_ok)} generator-embedded symbol(s) "
                      "allowed (checks.lib_refs.allow_embedded) and bound to pin-map", embedded_ok)
    return Result(name, PASS, f"{n} symbol/footprint reference(s) resolve through the project lib tables")


def embedded_policy(project: Project) -> dict[str, str] | None:
    """checks.lib_refs.allow_embedded: true -> {lib_symbol: constraints file} for parts pin-map can verify.
    None when embedded symbols are not allowed (the default: symbols come from vetted libraries)."""
    cfg = ((project.config.get("checks") or {}).get("lib_refs") or {})
    if not cfg.get("allow_embedded"):
        return None
    import yaml

    out: dict[str, str] = {}
    d = project.constraints_dir
    for f in sorted(d.glob("*.yaml")) if d.is_dir() else []:
        if f.name.endswith(".example.yaml"):
            continue
        try:
            c = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        m = c.get("match") or {}
        if isinstance(m, dict) and m.get("lib_symbol"):
            out.setdefault(str(m["lib_symbol"]), f.name)
    return out
