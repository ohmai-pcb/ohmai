# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Checks for the failures that mattered on generator-drawn sheets (dogfooding feedback):

gen-netlist  every pin-to-net the generator intends must appear in the KiCad-exported netlist: same net
             for all pins of an intended net (split = a coordinate missed a pin or a wire was truncated),
             no two intended nets merged, NC pins unconnected. Intent comes from
             hardware/gen/layout/<sheet>.yaml `parts.<ref>.nets` (ohmai schematic build), or for a foreign
             generator from ohmai.yaml checks.gen_netlist.intent: a CSV of `ref.pin,net` rows that the
             generator writes from its own pin map.
wire-ends    geometry per sheet: a wire end that touches nothing (no pin, wire, label, junction, sheet pin,
             no-connect) is a truncated wire; a pin sitting on the interior of a wire (not an end) is an
             ambiguous connection (KiCad connects pins at wire ends; break the wire at the pin).
cross-sheet  every net that spans more than one sheet has an expected_nets.csv row naming at least one
             exact pin on each sheet it reaches (the project-wide gate after the per-sheet gates).
             FAIL with checks.cross_sheet_strict: true (template default), WARN otherwise.
"""

from __future__ import annotations

import csv
from collections import defaultdict

import yaml

from ohmai.checks import FAIL, PASS, SKIP, WARN, Result, register
from ohmai.netlist import Netlist, base
from ohmai.project import Project
from ohmai.schgeo import parse_libsym, pin_point
from ohmai.sexp import find, first, parse


# ------------------------------------------------------------------ intent
def load_intent(project: Project) -> tuple[dict[str, str], str] | None:
    """{'U1.3': 'VIN', 'U1.5': 'NC', ...} and where it came from, or None when there is no generator intent."""
    cfg = ((project.config.get("checks") or {}).get("gen_netlist") or {})
    if cfg.get("intent"):
        f = project.root / cfg["intent"]
        if not f.is_file():
            raise FileNotFoundError(f"checks.gen_netlist.intent: {cfg['intent']} not found")
        out = {}
        with open(f, newline="", encoding="utf-8") as fh:
            for row in csv.reader(ln for ln in fh if ln.strip() and not ln.lstrip().startswith("#")):
                cells = [c.strip() for c in row]
                if len(cells) >= 2 and "." in cells[0] and cells[0].lower() != "ref.pin":
                    out[cells[0]] = cells[1]
        return out, cfg["intent"]
    ld = project.schematic.parent / "gen" / "layout"
    if not (ld / "_root.yaml").is_file():
        return None
    out = {}
    for f in sorted(ld.glob("*.yaml")):
        if f.name.startswith("_"):
            continue
        spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for ref, p in (spec.get("parts") or {}).items():
            for pin, net in ((p or {}).get("nets") or {}).items():
                out[f"{ref}.{pin}"] = str(net)
    return out, "hardware/gen/layout/*.yaml"


def compare_intent(nl: Netlist, intent: dict[str, str], refs: set[str] | None = None) -> tuple[list[str], list[str]]:
    errors, warns = [], []
    by_net: dict[str, list[str]] = defaultdict(list)
    for pin, net in intent.items():
        if refs is None or pin.split(".")[0] in refs:
            by_net[net].append(pin)
    if not nl.comps:
        return ["empty: KiCad netlist has 0 components (sheet did not load?)"], []
    owner: dict[str, str] = {}
    for net, pins in sorted(by_net.items()):
        if net == "NC":
            for p in pins:
                if p in nl.where and len(nl.nets[nl.where[p]]) > 1:
                    errors.append(f"nc: generator says {p} is NC, KiCad joins it to {base(nl.where[p])}")
            continue
        missing = [p for p in pins if p not in nl.where]
        for p in missing:
            ref = p.split(".")[0]
            why = "part missing from the netlist" if ref not in nl.comps else "pin not in the netlist (no such pin?)"
            errors.append(f"missing: {net}: {p}: {why}")
        groups: dict[str, list[str]] = defaultdict(list)
        for p in pins:
            if p in nl.where:
                groups[nl.where[p]].append(p)
        if len(groups) > 1:
            parts = "; ".join(f"{base(k)}: {', '.join(sorted(v)[:5])}" for k, v in sorted(groups.items()))
            errors.append(f"split: generator net {net} is {len(groups)} KiCad nets ({parts}): a wire end or "
                          "coordinate missed a pin, or a wire stops at an interior junction")
        for k, v in groups.items():
            if k in owner and owner[k] != net:
                errors.append(f"merged: generator nets {owner[k]} and {net} are one KiCad net {base(k)}")
            owner.setdefault(k, net)
            for p in v:
                if len(nl.nets[k]) == 1 and len(pins) > 1:
                    errors.append(f"unconnected: {p} ({net}) is alone in KiCad (pin not reached by any wire)")
            b = base(k)
            if not b.startswith(("Net-(", "unconnected-(")) and b != net and len(groups) == 1:
                warns.append(f"name: generator net {net} is named {b} in KiCad")
    return errors, warns


@register("gen-netlist", "Generator intent (gen/layout yaml or checks.gen_netlist.intent CSV) vs the KiCad-exported "
                         "netlist: every intended pin-to-net present, no split/merged nets, NC pins open")
def gen_netlist(project: Project, opts: dict) -> Result:
    from ohmai import kicad
    from ohmai.checks.schematic import export_netlist
    from ohmai.netlist import load

    intent = load_intent(project)
    if intent is None:
        return Result("gen-netlist", SKIP, "no generator intent (no hardware/gen/layout/_root.yaml, no "
                      "checks.gen_netlist.intent); hand-drawn sheets are covered by netlist")
    if not opts.get("netlist") and not kicad.kicad_cli():
        return Result("gen-netlist", SKIP, "kicad-cli not found")
    try:
        nl = load(export_netlist(project, opts))
    except RuntimeError as e:
        return Result("gen-netlist", FAIL, str(e))
    sh = opts.get("sheet")
    refs = nl.refs_on(sh.path) if sh else None
    pins, src = intent
    errors, warns = compare_intent(nl, pins, refs)
    n = sum(1 for p in pins if refs is None or p.split(".")[0] in refs)
    summary = (f"sheet {sh.name}: " if sh else "") + f"{n} intended pin(s) from {src}; {len(errors)} error(s)"
    if errors:
        return Result("gen-netlist", FAIL, summary, errors + warns)
    return Result("gen-netlist", WARN if warns else PASS, summary, warns)


# ------------------------------------------------------------------ wire geometry
def _k(x, y):
    return (round(float(x), 2), round(float(y), 2))


def wire_problems(text: str, sheet: str = "sheet") -> list[str]:
    tree = parse(text)
    libs = {s[1]: parse_libsym(s) for s in find(first(tree, "lib_symbols") or [], "symbol")}
    pins: dict = {}
    for s in find(tree, "symbol"):
        lid, at = first(s, "lib_id"), first(s, "at")
        if not lid or not at or lid[1] not in libs:
            continue
        x, y = float(at[1]), float(at[2])
        rot = int(float(at[3])) if len(at) > 3 else 0
        mir = first(s, "mirror")
        ref = next((p[2] for p in find(s, "property") if len(p) > 2 and p[1] == "Reference"), "?")
        for pin in libs[lid[1]].pins:
            pins.setdefault(_k(*pin_point(x, y, rot, pin, mir[1] if mir else None)), f"{ref}.{pin.num}")
    wires = []
    for w in find(tree, "wire"):
        xy = find(first(w, "pts"), "xy")
        wires.append((_k(xy[0][1], xy[0][2]), _k(xy[1][1], xy[1][2])))
    anchors = set()
    for kind in ("label", "global_label", "hierarchical_label", "junction", "no_connect"):
        for n in find(tree, kind):
            at = first(n, "at")
            anchors.add(_k(at[1], at[2]))
    for sh in find(tree, "sheet"):
        for p in find(sh, "pin"):
            at = first(p, "at")
            if at:
                anchors.add(_k(at[1], at[2]))

    def on_interior(p, a, b):
        if a[0] == b[0] == p[0]:
            return min(a[1], b[1]) < p[1] < max(a[1], b[1])
        if a[1] == b[1] == p[1]:
            return min(a[0], b[0]) < p[0] < max(a[0], b[0])
        return False

    out = []
    ends: dict = defaultdict(int)
    for a, b in wires:
        ends[a] += 1
        ends[b] += 1
    for i, (a, b) in enumerate(wires):
        for p in (a, b):
            if p in pins or p in anchors or ends[p] > 1:
                continue
            if any(on_interior(p, c, d) for j, (c, d) in enumerate(wires) if j != i):
                continue
            out.append(f"{sheet}: truncated wire: end ({p[0]:g}, {p[1]:g}) connects to nothing")
    for p, name in sorted(pins.items()):
        if p in ends:
            continue
        hit = [w for w in wires if on_interior(p, *w)]
        if hit:
            out.append(f"{sheet}: pin {name} at ({p[0]:g}, {p[1]:g}) sits mid-wire; end the wire on the pin")
    return out


@register("wire-ends", "No truncated wires (an end that touches nothing) and no pins sitting mid-wire, per sheet")
def wire_ends(project: Project, opts: dict) -> Result:
    from ohmai.checks.schematic import sheet_files

    if not project.schematic.is_file():
        return Result("wire-ends", FAIL, f"root schematic {project.schematic} not found")
    files, missing = sheet_files(project.schematic.resolve())
    sh = opts.get("sheet")
    if sh is not None:
        files = [f for f in files if f == sh.file]
    probs = [f"referenced sheet missing: {m}" for m in missing] if sh is None else []
    for f in files:
        probs += wire_problems(f.read_text(encoding="utf-8"), f.name)
    if probs:
        return Result("wire-ends", FAIL, f"{len(probs)} wire problem(s) in {len(files)} sheet(s)", probs)
    return Result("wire-ends", PASS, f"{len(files)} sheet(s): every wire end lands on a pin, wire, label or junction")


# ------------------------------------------------------------------ cross-sheet
@register("cross-sheet", "Every net that spans sheets has an expected_nets.csv row with an exact pin on each sheet it "
                         "reaches (project-wide gate, after the per-sheet gates)")
def cross_sheet(project: Project, opts: dict) -> Result:
    from ohmai import kicad
    from ohmai.checks import netdiff as nd
    from ohmai.checks.schematic import export_netlist
    from ohmai.netlist import load

    if not opts.get("netlist") and not kicad.kicad_cli():
        return Result("cross-sheet", SKIP, "kicad-cli not found")
    if not project.expected_nets.is_file():
        return Result("cross-sheet", FAIL, f"{project.expected_nets.name} not found")
    try:
        nl = load(export_netlist(project, opts))
    except RuntimeError as e:
        return Result("cross-sheet", FAIL, str(e))
    rows, _todo = nd.load_expected(project.expected_nets)
    exact_where = defaultdict(set)  # kicad net -> exact pins named by rows
    for _name, nodes, _o in rows:
        for n in nodes:
            if not n.endswith(".*") and n in nl.where:
                exact_where[nl.where[n]].add(n)
    errors, n_cross = [], 0
    for net, nodes in sorted(nl.nets.items()):
        sheets = {nl.comps.get(n.split(".")[0], {}).get("sheet", "/") for n in nodes}
        if len(sheets) < 2:
            continue
        n_cross += 1
        named = {nl.comps.get(n.split(".")[0], {}).get("sheet", "/") for n in exact_where.get(net, ())}
        lack = sorted(sheets - named)
        if lack:
            errors.append(f"{base(net)} spans {len(sheets)} sheets; expected_nets.csv names no exact pin on "
                          f"{', '.join(lack)}")
    if errors:
        strict = bool((project.config.get("checks") or {}).get("cross_sheet_strict", False))
        return Result("cross-sheet", FAIL if strict else WARN,
                      f"{n_cross} cross-sheet net(s); {len(errors)} not pinned down"
                      + ("" if strict else " (WARN; checks.cross_sheet_strict: true makes it FAIL)"), errors)
    return Result("cross-sheet", PASS, f"{n_cross} cross-sheet net(s), each pinned on every sheet it reaches")

