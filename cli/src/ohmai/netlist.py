"""Read a KiCad kicadsexpr netlist into plain dicts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ohmai.sexp import find, first, parse


@dataclass
class Netlist:
    comps: dict[str, dict] = field(default_factory=dict)  # ref -> {value, part, fields, footprint, flags, sheet}
    libpins: dict[str, dict[str, str]] = field(default_factory=dict)  # "lib:part" -> {num: name}
    nets: dict[str, set[str]] = field(default_factory=dict)  # net -> {"R1.1", ...}
    where: dict[str, str] = field(default_factory=dict)  # "R1.1" -> net

    def refs_on(self, sheet_path: str) -> set[str]:
        """Refs placed on the sheet instance '/name/' (KiCad sheetpath names)."""
        return {r for r, c in self.comps.items() if c.get("sheet", "/") == sheet_path}

    def pins_of(self, ref: str) -> dict[str, str]:
        c = self.comps.get(ref, {})
        return self.libpins.get(c.get("part", ""), {})


def _val(node, key, default=""):
    n = first(node, key) if node else None
    return n[1] if n and len(n) > 1 else default


def load(path: Path) -> Netlist:
    root = parse(Path(path).read_text(encoding="utf-8"))
    nl = Netlist()
    for c in find(first(root, "components") or [], "comp"):
        ref = _val(c, "ref")
        ls = first(c, "libsource")
        lib, part = (_val(ls, "lib"), _val(ls, "part")) if ls else ("", "")
        fields = {}
        for f in find(first(c, "fields") or [], "field"):
            name = _val(f, "name")
            fields[name] = f[2] if len(f) > 2 and isinstance(f[2], str) else ""
        flags = {_val(pr, "name") for pr in find(c, "property") if not first(pr, "value")}
        sp = first(c, "sheetpath")
        nl.comps[ref] = {"value": _val(c, "value"), "part": f"{lib}:{part}" if lib else part, "fields": fields,
                         "footprint": _val(c, "footprint"), "flags": flags,
                         "sheet": _val(sp, "names", "/") if sp else "/"}
    for lp in find(first(root, "libparts") or [], "libpart"):
        key = f"{_val(lp, 'lib')}:{_val(lp, 'part')}" if _val(lp, "lib") else _val(lp, "part")
        nl.libpins[key] = {_val(p, "num"): _val(p, "name") for p in find(first(lp, "pins") or [], "pin")}
    for n in find(first(root, "nets") or [], "net"):
        name = _val(n, "name")
        nodes = set()
        for nd in find(n, "node"):
            key = f"{_val(nd, 'ref')}.{_val(nd, 'pin')}"
            nodes.add(key)
            nl.where[key] = name
        nl.nets[name] = nodes
    return nl


def base(name: str) -> str:
    """'/sheet/NET' -> 'NET'."""
    return name.rstrip("/").split("/")[-1]


_SI = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "R": 1.0}


def parse_value(v: str) -> float | None:
    """'100n' -> 1e-7, '4k7' -> 4700, '0.22uF' -> 2.2e-7, '10R' -> 10. None if unparseable."""
    m = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*([pnuµμmkKMR]?)([0-9]*)", v or "")
    if not m:
        return None
    num = m.group(1)
    if m.group(3) and "." not in num:
        num = f"{num}.{m.group(3)}"
    try:
        return float(num) * _SI.get(m.group(2), 1.0)
    except ValueError:
        return None
