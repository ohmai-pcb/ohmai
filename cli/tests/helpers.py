# SPDX-License-Identifier: Apache-2.0
"""Build tiny kicadsexpr netlists for tests (no KiCad needed)."""

from __future__ import annotations

TPS63001_PINS = {
    "1": "SCL", "2": "SDA", "3": "ALT", "4": "FB", "5": "ISEN-", "6": "ISEN+", "7": "VOUT", "8": "BST2",
    "9": "HG2", "10": "VCC", "11": "AGND", "12": "PGND", "13": "SW2", "14": "SW1", "15": "PGND",
    "16": "IMON", "17": "EN", "18": "HG1", "19": "BST1", "20": "VIN",
}


def netlist(comps: dict[str, tuple[str, str]], nets: dict[str, list[str]], libparts: dict[str, dict]) -> str:
    """comps: ref -> (value, part); nets: name -> ["U1.1", ...]; libparts: part -> {num: name}."""
    out = ['(export (version "E")', "  (components"]
    for ref, (value, part) in comps.items():
        out.append(f'    (comp (ref "{ref}") (value "{value}") (libsource (lib "demo") (part "{part}")))')
    out.append("  )")
    out.append("  (libparts")
    for part, pins in libparts.items():
        ps = " ".join(f'(pin (num "{n}") (name "{nm}") (type "passive"))' for n, nm in pins.items())
        out.append(f'    (libpart (lib "demo") (part "{part}") (pins {ps}))')
    out.append("  )")
    out.append("  (nets")
    for i, (name, nodes) in enumerate(nets.items(), start=1):
        nd = " ".join(f'(node (ref "{n.split(".")[0]}") (pin "{n.split(".")[1]}"))' for n in nodes)
        out.append(f'    (net (code "{i}") (name "{name}") {nd})')
    out.append("  )")
    out.append(")")
    return "\n".join(out)


def good_tps63001():
    comps = {"U1": ("TPS63001", "TPS63001"), "C1": ("1u", "C"), "C2": ("0.22u", "C"), "C3": ("220n", "C"),
             "R1": ("4k7", "R"), "R2": ("100k", "R"), "R3": ("10k", "R")}
    nets = {
        "/VCC": ["U1.10", "C1.1"], "GND": ["C1.2", "U1.11", "U1.12", "U1.15", "R3.2"],
        "/SW1": ["U1.14", "C2.2"], "/BST1": ["U1.19", "C2.1"],
        "/SW2": ["U1.13", "C3.2"], "/BST2": ["U1.8", "C3.1"],
        "/SDA": ["U1.2", "R1.1"], "+3V3": ["R1.2"], "/FB": ["U1.4", "R2.2", "R3.1"], "/VOUT": ["U1.7", "R2.1"],
    }
    for n in ["1", "3", "5", "6", "9", "16", "17", "18", "20"]:
        nets[f"/N{n}"] = [f"U1.{n}"]
    libparts = {"TPS63001": TPS63001_PINS, "C": {"1": "~", "2": "~"}, "R": {"1": "~", "2": "~"}}
    return comps, nets, libparts
