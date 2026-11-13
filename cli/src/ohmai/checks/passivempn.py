# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`passive-mpn`: a resistor's or capacitor's MPN encodes the same value as its Value field.

Lesson from dogfooding: a resistor's Value said one thing and its MPN encoded another, so the BOM would have
ordered the wrong part. Values and MPNs are edited separately; this decodes the vendor codes that encode a
value and compares. Unknown MPN formats are listed, never guessed.
"""

from __future__ import annotations

import re

from ohmai import kicad
from ohmai.checks import FAIL, PASS, SKIP, WARN, Result, register
from ohmai.netlist import parse_value
from ohmai.project import Project


def _eia(code: str, sig: int) -> float:
    """'1002' (sig 3) -> 10000; '104' (sig 2) -> 100000."""
    return float(code[:sig]) * 10 ** int(code[sig])


def _yageo(v: str) -> float | None:
    v = v.upper()
    m = re.fullmatch(r"(\d+)([RKM])(\d*)", v)
    if not m:
        return None
    mult = {"R": 1, "K": 1e3, "M": 1e6}[m.group(2)]
    return float(f"{m.group(1)}.{m.group(3) or 0}") * mult


# (name, kind, regex, decoder) -> ohms for R, farads for C
DECODERS = [
    ("UniRoyal", "R", re.compile(r"^\d{4}W[A-Z0-9][A-Z](\d{4})T"), lambda m: _eia(m.group(1), 3)),
    ("Yageo RC", "R", re.compile(r"^RC\d{4}[A-Z]R-\d{2}([0-9RKM]+)L"), lambda m: _yageo(m.group(1))),
    ("Samsung CL", "C", re.compile(r"^CL\d{2}[A-Z](\d{3})[A-Z]"), lambda m: _eia(m.group(1), 2) * 1e-12),
    ("Murata GRM/GCM", "C", re.compile(r"^G[RC]M\d{3}[A-Z0-9]{2}\d[A-Z](\d{3})"), lambda m: _eia(m.group(1), 2) * 1e-12),
]


def decode(mpn: str) -> tuple[str, str, float] | None:
    """(vendor, kind, value) or None when the MPN format is not one we decode."""
    s = (mpn or "").strip().upper()
    for name, kind, rx, fn in DECODERS:
        m = rx.match(s)
        if m:
            val = fn(m)
            if val is not None:
                return name, kind, val
    return None


def compare(comps: dict[str, dict], tol: float = 0.011) -> tuple[list[str], list[str], int]:
    """(errors, unknown, n_checked) for R*/C* parts with an MPN."""
    errors, unknown, n = [], [], 0
    for ref in sorted(comps):
        kind = re.match(r"^([RC])\d", ref)
        if not kind:
            continue
        c = comps[ref]
        mpn = (c.get("fields") or {}).get("MPN") or c.get("mpn") or ""
        if not mpn:
            continue
        d = decode(mpn)
        if d is None:
            unknown.append(f"{ref}: MPN {mpn} format not decoded; check its value by hand")
            continue
        vendor, k, mval = d
        if k != kind.group(1):
            errors.append(f"{ref}: MPN {mpn} is a {vendor} {'resistor' if k == 'R' else 'capacitor'}")
            continue
        v = parse_value(str(c.get("value", "")))
        n += 1
        if v is None:
            errors.append(f"{ref}: value {c.get('value')!r} is not a number; MPN {mpn} encodes {mval:g}")
        elif abs(v - mval) > tol * max(abs(mval), 1e-15):
            errors.append(f"{ref}: value {c.get('value')} but MPN {mpn} ({vendor}) encodes {mval:g}"
                          f"{' ohm' if k == 'R' else ' F'}")
    return errors, unknown, n


def summarize(n: int, errors: list[str], unknown: list[str]) -> str:
    return f"{n} R/C MPN(s) decoded, {len(errors)} mismatch(es); skipped {len(unknown)} MPN(s) not decodable"


@register("passive-mpn", "Resistor/capacitor MPN encodes the same value as the Value field (UniRoyal, Yageo, Samsung, Murata)")
def passive_mpn(project: Project, opts: dict) -> Result:
    from ohmai.checks.schematic import export_netlist
    from ohmai.netlist import load

    if not (opts or {}).get("netlist") and not kicad.kicad_cli():
        return Result("passive-mpn", SKIP, "kicad-cli not found")
    try:
        nl = load(export_netlist(project, opts))
    except (RuntimeError, FileNotFoundError) as e:
        return Result("passive-mpn", FAIL, str(e))
    comps = nl.comps
    sh = (opts or {}).get("sheet")
    if sh is not None:
        on = nl.refs_on(sh.path)
        comps = {r: c for r, c in comps.items() if r in on}
    errors, unknown, n = compare(comps)
    summary = summarize(n, errors, unknown)
    if errors:
        return Result("passive-mpn", FAIL, summary, errors + unknown)
    if n == 0:
        return Result("passive-mpn", SKIP, summary + " (no decodable MPN)", unknown)
    return Result("passive-mpn", WARN if unknown else PASS, summary, unknown)
