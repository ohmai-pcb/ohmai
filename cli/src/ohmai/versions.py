"""Version strings and ranges (shared by the kicad-app check and the toolchain lock)."""

from __future__ import annotations

import re

_SPEC = re.compile(r"^\s*(>=|<=|==|!=|>|<)?\s*v?(\d+(?:\.(?:\d+|x|\*))*)\s*$")
_VER = re.compile(r"(\d+(?:\.\d+)+|\d+)")


def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def first_version(text: str) -> str | None:
    """First dotted version number in a tool's output: 'kibot 1.8.2 - ...' -> '1.8.2'."""
    m = _VER.search(text or "")
    return m.group(1) if m else None


def version_in_range(version: str, spec: str) -> tuple[bool, str]:
    """`spec`: comma/space-separated clauses like ">=9.0.2,<9.1", or a wildcard "9.0.x" (== 9.0.*).
    Returns (ok, error); error is set when the spec does not parse."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", version or "")
    if not m:
        return False, f"cannot read a version from {version!r}"
    have = vtuple(m.group(1))
    clauses = [c for c in re.split(r"[,\s]+(?=[<>=!]|\d)", str(spec).strip()) if c.strip()]
    if not clauses:
        return False, "empty version range"
    for c in clauses:
        cm = _SPEC.match(c)
        if not cm:
            return False, f"cannot parse version clause {c!r} (use e.g. '>=9.0.2,<9.1' or '9.0.x')"
        op, ver = cm.group(1) or "==", cm.group(2)
        if ver.endswith((".x", ".*")) or op == "==" and "x" in ver:
            pre = vtuple(ver)
            hit = have[:len(pre)] == pre
            if (op in ("==",) and not hit) or (op == "!=" and hit):
                return False, ""
            if op not in ("==", "!="):
                return False, f"wildcard only with == / != in {c!r}"
            continue
        want = vtuple(ver)
        n = max(len(have), len(want))
        h, w = have + (0,) * (n - len(have)), want + (0,) * (n - len(want))
        ok = {">=": h >= w, "<=": h <= w, ">": h > w, "<": h < w, "==": h == w, "!=": h != w}[op]
        if not ok:
            return False, ""
    return True, ""
