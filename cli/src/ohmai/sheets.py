# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Sheet instances of a hierarchical schematic and the `--sheet <name>` scope used by per-sheet gates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ohmai.sexp import find, first, parse


@dataclass(frozen=True)
class Sheet:
    name: str      # instance name ("1-power"); "root" for the root sheet
    file: Path     # resolved .kicad_sch
    path: str      # KiCad sheetpath names ("/1-power/"; "/" for the root)


def _props(node) -> dict[str, str]:
    return {p[1]: p[2] for p in find(node, "property") if len(p) > 2 and isinstance(p[2], str)}


def instances(root_sch: Path) -> list[Sheet]:
    """Root plus every sheet instance reachable from it (depth first, nested paths)."""
    root_sch = Path(root_sch).resolve()
    out = [Sheet("root", root_sch, "/")]
    todo = [(root_sch, "/", 0)]
    while todo:
        f, prefix, depth = todo.pop(0)
        if depth > 16 or not f.is_file():
            continue
        try:
            tree = parse(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001 (a sheet that does not parse is reported by sheet-load)
            continue
        for s in find(tree, "sheet"):
            pr = _props(s)
            name = pr.get("Sheetname") or pr.get("Sheet name")
            sf = pr.get("Sheetfile") or pr.get("Sheet file")
            if not name or not sf:
                continue
            child = (f.parent / sf).resolve()
            sh = Sheet(name, child, f"{prefix}{name}/")
            out.append(sh)
            todo.append((child, sh.path, depth + 1))
    return out


def resolve(root_sch: Path, name: str) -> Sheet:
    """Sheet by instance name, path ('/1-power/'), file name or file stem. SystemExit when not found."""
    allx = instances(root_sch)
    n = name.strip()
    for s in allx:
        if n in (s.name, s.path, s.path.strip("/"), s.file.name, s.file.stem):
            return s
    raise SystemExit(f"--sheet {name}: no such sheet (have: {', '.join(s.name for s in allx)})")


def from_opts(opts: dict | None) -> Sheet | None:
    return (opts or {}).get("sheet")


def symbol_count(text: str) -> int:
    """Placed symbols + sheet symbols in one .kicad_sch text (not the lib_symbols cache, not power symbols)."""
    try:
        tree = parse(text)
    except Exception:  # noqa: BLE001
        return 0
    n = len(find(tree, "sheet"))  # a root that only holds sheet symbols is not empty
    for s in find(tree, "symbol"):
        lid = first(s, "lib_id")
        if not lid:
            continue
        ref = next((p[2] for p in find(s, "property") if len(p) > 2 and p[1] == "Reference"), "")
        if not str(ref).startswith("#"):
            n += 1
    return n
