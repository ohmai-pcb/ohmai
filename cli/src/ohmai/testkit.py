# SPDX-License-Identifier: Apache-2.0
"""Build small kicadsexpr netlists from YAML (known-bad fixtures, tests). No KiCad needed.

    parts:   {U1: {value: AP2112K-3.3, part: "Regulator_Linear:AP2112K-3.3", mpn: AP2112K-3.3TRG1}, ...}
    pins:    {"Regulator_Linear:AP2112K-3.3": {"1": VIN, ...}, "Device:C": {"1": "~", "2": "~"}}
    nets:    {VIN: [J1.1, U1.1], ...}

`part` is "lib:name" as in KiCad; `mpn` (optional) becomes the MPN field, `fields` adds others,
`footprint` sets the footprint, `flags` adds valueless properties (dnp, exclude_from_bom).
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _q(s) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_sexpr(spec: dict) -> str:
    out = ['(export (version "E")', "  (components"]
    for ref, p in (spec.get("parts") or {}).items():
        lib, _, name = str(p.get("part", "")).rpartition(":")
        fields = dict(p.get("fields") or {})
        if p.get("mpn"):
            fields["MPN"] = p["mpn"]
        fl = " ".join(f"(field (name {_q(k)}) {_q(v)})" for k, v in fields.items())
        out.append(f"    (comp (ref {_q(ref)}) (value {_q(p.get('value', ''))})"
                   + (f" (footprint {_q(p['footprint'])})" if p.get("footprint") else "")
                   + (f" (fields {fl})" if fl else "")
                   + f" (libsource (lib {_q(lib)}) (part {_q(name)}))"
                   + "".join(f" (property (name {_q(f)}))" for f in p.get("flags") or []) + ")")
    out += ["  )", "  (libparts"]
    for key, pins in (spec.get("pins") or {}).items():
        lib, _, name = str(key).rpartition(":")
        ps = " ".join(f"(pin (num {_q(n)}) (name {_q(nm)}) (type \"passive\"))" for n, nm in pins.items())
        out.append(f"    (libpart (lib {_q(lib)}) (part {_q(name)}) (pins {ps}))")
    out += ["  )", "  (nets"]
    for i, (name, nodes) in enumerate((spec.get("nets") or {}).items(), start=1):
        nd = " ".join(f"(node (ref {_q(n.split('.')[0])}) (pin {_q(n.split('.', 1)[1])}))" for n in nodes or [])
        out.append(f"    (net (code \"{i}\") (name {_q(name)}) {nd})")
    out += ["  )", ")"]
    return "\n".join(out) + "\n"


def compile_file(src: Path) -> Path:
    """board.net.yaml -> board.net next to it."""
    dst = src.with_name(src.name[: -len(".yaml")])
    dst.write_text(to_sexpr(yaml.safe_load(src.read_text(encoding="utf-8")) or {}), encoding="utf-8")
    return dst
