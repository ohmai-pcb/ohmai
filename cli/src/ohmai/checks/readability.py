# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`ohmai check readability`: is each sheet drawn like a datasheet application figure?

Per block sheet (the root only joins blocks and is exempt from the label rules):
  wire-path   a passive (R C L D Y FB F ...) pin on a signal net that an IC pin of the same sheet also
              uses must reach that IC pin by wire (through other parts' pins is fine: R in series with
              C is a path); joining them only through net labels fails.
  local-label local net labels inside a block fail unless allow-listed
              (ohmai.yaml checks.readability.allow_local_labels: fnmatch patterns, for long buses), or the
              project uses checks.readability.labels: rail-and-label (labels join blocks inside a sheet;
              the wire-path rule, i.e. passives wired to their IC pins, stays the hard gate). Global and
              hierarchical labels are never banned: they join sheets.
  junction    three or more connections at a point without a junction dot; a dot on a plain crossing
              or a four-way dot; wires crossing (allow with checks.readability.allow_crossings: true).
  grid        pins, wire ends, labels, junctions off the 1.27 mm grid.
  overlap     symbol bodies overlapping; wires running through a body; visible texts (fields, labels,
              power values) overlapping each other, a body, or a wire.
Text extents are ESTIMATES (stroke font ~0.78 x size per character); the reviewer still judges the PNG
renders against the datasheet reference figures.
"""

from __future__ import annotations

import fnmatch
import math
import re
from collections import defaultdict

from ohmai.checks import FAIL, PASS, WARN, Result, register
from ohmai.project import Project
from ohmai.schgeo import (
    body_box,
    field_box,
    on_grid,
    overlap,
    parse_libsym,
    pin_point,
    seg_hits_box,
    text_box,
    text_width,
    xform,
)
from ohmai.sexp import find, first, parse

PIN_TEXT = 0.5      # mm around a visible pin line (pin number / name) that a field must not touch
POWER_CLEAR = 0.8   # mm around a power symbol's net name kept free of other nodes' wires and junctions
DNP_CROSS = 1.3     # mm the DNP cross reaches beyond a symbol body (ESTIMATE)
TEXT_GAP = 1.0     # mm kept between a part text and a power symbol's net name
TITLE_SIZE = 2.4    # mm, ESTIMATE of the title-block title font (bold) in text_width units
TITLE_MAX = 102.0   # mm, ESTIMATE of the title field width in the KiCad default worksheet

PASSIVE_PREFIX = ("R", "C", "L", "D", "Y", "FB", "F", "RN", "X")
IC_PREFIX = ("U", "IC", "Q")


def _prefix(ref: str) -> str:
    m = re.match(r"[A-Za-z_#]+", ref)
    return m.group(0) if m else ""


def _hidden(node) -> bool:
    eff = first(node, "effects")
    if not eff:
        return False
    for c in eff:
        if c == "hide":
            return True
        if isinstance(c, list) and c and c[0] == "hide" and (len(c) < 2 or c[1] == "yes"):
            return True
    return False


def _size(node) -> float:
    eff = first(node, "effects")
    f = first(eff, "font") if eff else None
    s = first(f, "size") if f else None
    return float(s[1]) if s else 1.27


def _justify(node) -> str:
    eff = first(node, "effects")
    j = first(eff, "justify") if eff else None
    return " ".join(str(x) for x in j[1:]) if j else ""


def _k(x, y):
    return (round(x, 3), round(y, 3))


class _UF:
    def __init__(self):
        self.p = {}

    def f(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def u(self, a, b):
        self.p[self.f(a)] = self.f(b)


def analyze(text: str, sheet: str = "sheet", root: bool = False, allow_labels=(), allow_crossings=False,
            label_policy: str = "allowlist"):
    """Return (fails, warns) for one .kicad_sch text."""
    fails: list[str] = []
    warns: list[str] = []
    tree = parse(text)
    libs = {}
    ls_node = first(tree, "lib_symbols")
    hide_nums = set()
    for s in find(ls_node or [], "symbol"):
        libs[s[1]] = parse_libsym(s)
        pn = first(s, "pin_numbers")
        if pn and ("hide" in [x if isinstance(x, str) else x[0] for x in pn[1:]]):
            hide_nums.add(s[1])

    syms = []  # (ref, lib, x, y, rot, mirror, ls, power)
    texts = []  # (owner, text, box)
    pins = []  # (ref, num, x, y, power)
    pin_lines = []  # (ref, num, connection point, body end) of visible pins
    pin_nums = []  # (ref, num, box) of drawn pin numbers (ESTIMATE of KiCad's placement)
    power_texts = []  # (ref, value, box, own pin points)
    dnp: set[str] = set()
    for s in find(tree, "symbol"):
        lid = first(s, "lib_id")
        at = first(s, "at")
        if not lid or not at:
            continue
        ls = libs.get(lid[1])
        if ls is None:
            fails.append(f"{sheet}: symbol {lid[1]} not embedded")
            continue
        x, y = float(at[1]), float(at[2])
        rot = int(float(at[3])) if len(at) > 3 else 0
        mir = first(s, "mirror")
        mirror = mir[1] if mir else None
        props = {p[1]: p for p in find(s, "property")}
        ref = props["Reference"][2] if "Reference" in props else "?"
        syms.append((ref, lid[1], x, y, rot, mirror, ls))
        dnp_n = first(s, "dnp")
        if dnp_n and len(dnp_n) > 1 and dnp_n[1] == "yes":
            dnp.add(ref)
        for pin in ls.pins:
            px, py = pin_point(x, y, rot, pin, mirror)
            pins.append((ref, pin.num, px, py, ls.power))
            if not ls.power and not pin.hidden and pin.length > 0:
                ex, ey = xform(rot, pin.x + pin.length * math.cos(math.radians(pin.ang)),
                               pin.y + pin.length * math.sin(math.radians(pin.ang)), mirror)
                q = (round(x + ex, 4), round(y + ey, 4))
                pin_lines.append((ref, pin.num, (px, py), q))
                if lid[1] not in hide_nums:
                    pin_nums.append((ref, pin.num, _num_box(pin.num, (px, py), q)))
            if not (on_grid(px) and on_grid(py)):
                fails.append(f"{sheet}: grid: {ref} pin {pin.num} at ({px:g}, {py:g}) is off the 1.27 mm grid")
        for name in ("Reference", "Value"):
            p = props.get(name)
            if p is None or _hidden(p) or (name == "Reference" and ref.startswith("#")) or not str(p[2]):
                continue
            pat = first(p, "at")
            box = field_box(str(p[2]), float(pat[1]), float(pat[2]), float(pat[3]) if len(pat) > 3 else 0.0,
                            _size(p), _justify(p), rot, mirror)
            texts.append((f"{ref} {name.lower()}", str(p[2]), box, ref))
            if ls.power and name == "Value":
                power_texts.append((ref, str(p[2]), box, {_k(*pin_point(x, y, rot, pn, mirror)) for pn in ls.pins}))

    wires = []
    for w in find(tree, "wire"):
        xy = find(first(w, "pts"), "xy")
        a, b = (float(xy[0][1]), float(xy[0][2])), (float(xy[1][1]), float(xy[1][2]))
        wires.append((a, b))
        for p in (a, b):
            if not (on_grid(p[0]) and on_grid(p[1])):
                fails.append(f"{sheet}: grid: wire end ({p[0]:g}, {p[1]:g}) is off the 1.27 mm grid")
        if abs(a[0] - b[0]) > 1e-6 and abs(a[1] - b[1]) > 1e-6:
            fails.append(f"{sheet}: diagonal wire ({a[0]:g},{a[1]:g})-({b[0]:g},{b[1]:g})")
    junctions = []
    for j in find(tree, "junction"):
        at = first(j, "at")
        junctions.append((float(at[1]), float(at[2])))
    labels = []  # (kind, name, x, y)
    for kind in ("label", "hierarchical_label", "global_label"):
        for lb in find(tree, kind):
            at = first(lb, "at")
            x, y, ang = float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0
            labels.append((kind, lb[1], x, y))
            pad = _size(lb) * 1.5 if kind != "label" else 0.0
            box = text_box(lb[1], x + (pad if ang == 0 else -pad if ang == 180 else 0),
                           y + (-pad if ang == 90 else pad if ang == 270 else 0), ang, _size(lb), _justify(lb))
            texts.append((f"label {lb[1]}", lb[1], box, None))
    for x, y in junctions + [(lb[2], lb[3]) for lb in labels]:
        if not (on_grid(x) and on_grid(y)):
            fails.append(f"{sheet}: grid: item at ({x:g}, {y:g}) is off the 1.27 mm grid")

    # ---- connectivity (wire ends, T onto wire interiors, pins on wire ends/interiors)
    uf = _UF()

    def on_seg(p, a, b):
        return (min(a[0], b[0]) - 1e-6 <= p[0] <= max(a[0], b[0]) + 1e-6 and
                min(a[1], b[1]) - 1e-6 <= p[1] <= max(a[1], b[1]) + 1e-6 and
                (abs(a[0] - b[0]) < 1e-6 or abs(a[1] - b[1]) < 1e-6))

    for i, (a, b) in enumerate(wires):
        uf.u(("w", i), _k(*a))
        uf.u(("w", i), _k(*b))
    pts = {_k(*a) for a, _ in wires} | {_k(*b) for _, b in wires} | {_k(p[2], p[3]) for p in pins} | \
        {_k(lb[2], lb[3]) for lb in labels}
    for p in pts:
        for i, (a, b) in enumerate(wires):
            if on_seg(p, a, b):
                uf.u(("w", i), p)
    # connection count per point, for junction rules
    for p in pts:
        n = 0
        interior = 0
        for a, b in wires:
            if _k(*a) == p or _k(*b) == p:
                n += 1
            elif on_seg(p, a, b):
                interior += 1
        n_pins = len({q[0] for q in pins if _k(q[2], q[3]) == p})  # stacked pins of one symbol count once
        total = n + 2 * interior + n_pins
        has_j = any(_k(*j) == p for j in junctions)
        if total >= 3 and not has_j and (n + interior) > 0:
            fails.append(f"{sheet}: junction: {total} connections meet at ({p[0]:g}, {p[1]:g}) without a junction dot")
        if total >= 4 and has_j:
            fails.append(f"{sheet}: junction: four-way junction at ({p[0]:g}, {p[1]:g}) (reads like a crossing; offset one branch)")
    for j in junctions:
        p = _k(*j)
        ends = sum(1 for a, b in wires if _k(*a) == p or _k(*b) == p)
        if ends == 0 and sum(1 for a, b in wires if on_seg(p, a, b)) >= 2:
            fails.append(f"{sheet}: junction: dot on a plain wire crossing at ({p[0]:g}, {p[1]:g})")
        elif p not in pts and ends == 0:
            warns.append(f"{sheet}: junction at ({p[0]:g}, {p[1]:g}) on nothing")
    if not allow_crossings:
        for i, (a, b) in enumerate(wires):
            for c, d in wires[i + 1:]:
                h1, v2 = abs(a[1] - b[1]) < 1e-6, abs(c[0] - d[0]) < 1e-6
                if h1 and v2:
                    hx, vx = (a, b), (c, d)
                elif abs(c[1] - d[1]) < 1e-6 and abs(a[0] - b[0]) < 1e-6:
                    hx, vx = (c, d), (a, b)
                else:
                    continue
                X, Y = vx[0][0], hx[0][1]
                if (min(hx[0][0], hx[1][0]) + 1e-6 < X < max(hx[0][0], hx[1][0]) - 1e-6 and
                        min(vx[0][1], vx[1][1]) + 1e-6 < Y < max(vx[0][1], vx[1][1]) - 1e-6):
                    fails.append(f"{sheet}: junction: wires cross at ({X:g}, {Y:g}) (re-route, or allow_crossings)")
    # collinear overlapping wires are ambiguous too
    for i, (a, b) in enumerate(wires):
        for c, d in wires[i + 1:]:
            if abs(a[1] - b[1]) < 1e-6 and abs(c[1] - d[1]) < 1e-6 and abs(a[1] - c[1]) < 1e-6:
                lo, hi = max(min(a[0], b[0]), min(c[0], d[0])), min(max(a[0], b[0]), max(c[0], d[0]))
            elif abs(a[0] - b[0]) < 1e-6 and abs(c[0] - d[0]) < 1e-6 and abs(a[0] - c[0]) < 1e-6:
                lo, hi = max(min(a[1], b[1]), min(c[1], d[1])), min(max(a[1], b[1]), max(c[1], d[1]))
            else:
                continue
            if hi - lo > 1e-6:
                fails.append(f"{sheet}: junction: overlapping collinear wires near ({a[0]:g}, {a[1]:g})")

    # ---- nets per component: label names and power symbol values
    comp_names: dict = defaultdict(set)
    for _kind, name, x, y in labels:
        comp_names[uf.f(_k(x, y))].add(name)
    power_comp = set()
    for _ref, _lid, x, y, rot, mirror, ls in syms:
        if ls.power:
            for pin in ls.pins:
                c = uf.f(_k(*pin_point(x, y, rot, pin, mirror)))
                power_comp.add(c)
    # pins of a symbol are joined to each other only through other parts; a passive's own two pins are
    # a path (series R-C is a wire path from the IC to the C)
    graph = defaultdict(set)
    pin_comp = {}
    for ref, num, x, y, _pw in pins:
        c = uf.f(_k(x, y))
        pin_comp[(ref, num)] = c
    by_ref = defaultdict(list)
    for (ref, _num), c in pin_comp.items():
        by_ref[ref].append(c)
    for ref, cs in by_ref.items():
        if _prefix(ref) in PASSIVE_PREFIX or _prefix(ref) in ("JP", "SJ", "NT"):
            for a in cs:
                for b in cs:
                    if a != b:
                        graph[a].add(b)

    def reach(c0, limit=4):
        seen, todo = {c0}, [(c0, 0)]
        while todo:
            c, d = todo.pop()
            if d >= limit:
                continue
            for n in graph[c]:
                if n not in seen:
                    seen.add(n)
                    todo.append((n, d + 1))
        return seen

    if not root:
        ic_by_net = defaultdict(list)
        for (ref, num), c in pin_comp.items():
            if _prefix(ref) in IC_PREFIX:
                for nm in comp_names.get(c, ()):
                    ic_by_net[nm].append((ref, num, c))
        for (ref, num), c in sorted(pin_comp.items()):
            if _prefix(ref) not in PASSIVE_PREFIX or c in power_comp:
                continue
            for nm in sorted(comp_names.get(c, ())):
                ics = [x for x in ic_by_net.get(nm, []) if x[2] != c]
                if not ics:
                    continue
                r = reach(c)
                if not any(x[2] in r for x in ic_by_net[nm]):
                    fails.append(f"{sheet}: wire-path: {ref}.{num} reaches {ics[0][0]}.{ics[0][1]} ({nm}) only through a "
                                 f"label; draw the wire (datasheet-figure style)")
        for kind, name, _x, _y in labels:
            if kind == "label" and label_policy != "rail-and-label" and not any(fnmatch.fnmatchcase(name, pat) for pat in allow_labels):
                fails.append(f"{sheet}: local-label: '{name}' inside a block (wire it, make it a port, or allow-list a bus)")

    # ---- overlaps
    bodies = []
    for ref, _lid, x, y, rot, mirror, ls in syms:
        b = body_box(ls, x, y, rot, mirror)
        if b:
            bodies.append((ref, b, set(_k(*pin_point(x, y, rot, p, mirror)) for p in ls.pins)))
    for i, (r1, b1, _p) in enumerate(bodies):
        for r2, b2, _q in bodies[i + 1:]:
            if overlap(b1, b2, 0.1):
                fails.append(f"{sheet}: overlap: symbols {r1} and {r2} overlap")
    for a, b in wires:
        for ref, bx, pp in bodies:
            if ref.startswith("#"):
                continue
            if _k(*a) in pp or _k(*b) in pp:  # a stub leaving the symbol's own pin
                continue
            if seg_hits_box(a, b, bx, 0.4):
                fails.append(f"{sheet}: overlap: wire ({a[0]:g},{a[1]:g})-({b[0]:g},{b[1]:g}) runs through {ref}")
    for i, (o1, t1, b1, own1) in enumerate(texts):
        for o2, t2, b2, _own2 in texts[i + 1:]:
            if overlap(b1, b2, 0.15):
                fails.append(f"{sheet}: overlap: text '{t1}' ({o1}) and '{t2}' ({o2})")
            elif (o1.startswith("#") != o2.startswith("#")) and overlap(b1, b2, -TEXT_GAP):
                # a part's text touching a power symbol's net name reads as one string (F14 '5.1k' + 'GND')
                fails.append(f"{sheet}: overlap: text '{t1}' ({o1}) touches '{t2}' ({o2})")
        for ref, bx, _pp in bodies:
            if ref != own1 and overlap(b1, bx, 0.25):
                fails.append(f"{sheet}: overlap: text '{t1}' ({o1}) over symbol {ref}")
            elif ref != own1 and ref in dnp and overlap(b1, _grow(bx, DNP_CROSS), 0.1):
                fails.append(f"{sheet}: overlap: text '{t1}' ({o1}) over the DNP cross of {ref}")
        for ref, num, nb in pin_nums:
            if overlap(b1, nb, 0.05):
                fails.append(f"{sheet}: overlap: text '{t1}' ({o1}) on the pin number {num} of {ref}")
                break
        for ref, num, pa, pb in pin_lines:
            if seg_hits_box(pa, pb, _grow(b1, PIN_TEXT), 0.0):
                fails.append(f"{sheet}: overlap: text '{t1}' ({o1}) on pin {num} of {ref} (pin line / number)")
                break
        anchor = None
        if o1.startswith("label"):
            anchor = next((_k(lb[2], lb[3]) for lb in labels if f"label {lb[1]}" == o1 and
                           b1[0] - 3 <= lb[2] <= b1[2] + 3 and b1[1] - 3 <= lb[3] <= b1[3] + 3), None)
        for a, b in wires:
            if anchor is not None and anchor in (_k(*a), _k(*b)):
                continue
            if seg_hits_box(a, b, b1, 0.2):
                fails.append(f"{sheet}: overlap: wire through text '{t1}' ({o1})")
                break
    # ---- a power symbol's net name must not sit on another node (F1: 'VSYS' under the VINA junction)
    for ref, val, box, own in power_texts:
        near = _grow(box, POWER_CLEAR)
        for jx, jy in junctions:
            if _k(jx, jy) not in own and near[0] <= jx <= near[2] and near[1] <= jy <= near[3]:
                fails.append(f"{sheet}: ambiguous: power symbol '{val}' ({ref}) text next to the junction at "
                             f"({jx:g}, {jy:g}) of another node; move the symbol clear of it")
                break
        else:
            for a, b in wires:
                if _k(*a) in own or _k(*b) in own:
                    continue
                if seg_hits_box(a, b, near, 0.0):
                    fails.append(f"{sheet}: ambiguous: power symbol '{val}' ({ref}) text touches a wire "
                                 f"({a[0]:g},{a[1]:g})-({b[0]:g},{b[1]:g}) of another node")
                    break
    # ---- title block: the title must fit its field (ESTIMATE of the KiCad title font)
    tb = first(tree, "title_block")
    tt = first(tb, "title") if tb else None
    if tt and len(tt) > 1:
        w = text_width("Title: " + str(tt[1]), TITLE_SIZE)
        if w > TITLE_MAX:
            fails.append(f"{sheet}: title: '{tt[1]}' is ≈{w:.0f} mm, the title field holds ≈{TITLE_MAX:.0f} mm "
                         f"(ESTIMATE); shorten it")
    return sorted(set(fails)), sorted(set(warns))


def _num_box(num: str, a, b, size: float = 1.27, gap: float = 0.4):
    """Pin number box: centred on the pin line, above a horizontal pin, left of a vertical one (ESTIMATE)."""
    w = text_width(num, size)
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    if abs(a[1] - b[1]) < 1e-6:
        return (mx - w / 2, my - gap - size, mx + w / 2, my - gap)
    return (mx - gap - size, my - w / 2, mx - gap, my + w / 2)


def _grow(b, d):
    return (b[0] - d, b[1] - d, b[2] + d, b[3] + d)


@register("readability", "Sheets read like datasheet application figures: passives wired to their IC pins (no "
                         "label-only joins), no local labels inside blocks (allow-list for buses), unambiguous "
                         "junctions/crossings, on-grid, no overlapping symbols/texts")
def readability(project: Project, opts: dict) -> Result:
    from ohmai.checks.schematic import sheet_files

    cfg = ((project.config.get("checks") or {}).get("readability") or {})
    allow = cfg.get("allow_local_labels") or []
    files, missing = sheet_files(project.schematic)
    if missing:
        return Result("readability", FAIL, f"missing sheets: {', '.join(missing)}")
    policy = str(cfg.get("labels") or "allowlist")
    if policy not in ("allowlist", "rail-and-label"):
        return Result("readability", FAIL, f"checks.readability.labels: {policy!r} (use allowlist or rail-and-label)")
    sh = (opts or {}).get("sheet")
    fails, warns = [], []
    n_done = 0
    for f in files:
        if sh is not None and f.resolve() != sh.file:
            continue
        n_done += 1
        rel = f.name
        fl, wn = analyze(f.read_text(encoding="utf-8"), rel, root=(f.resolve() == project.schematic.resolve()),
                         allow_labels=allow, allow_crossings=bool(cfg.get("allow_crossings")), label_policy=policy)
        fails += fl
        warns += wn
    papers = {}
    for f in files:
        m = re.search(r'\(paper\s+"([^"]+)"(\s+portrait)?', f.read_text(encoding="utf-8"))
        papers[f.name] = (m.group(1) + (" portrait" if m.group(2) else "")) if m else "?"
    if len(set(papers.values())) > 1:
        fails.append("sheet size: all sheets must share one common paper size (the smallest that fits every "
                     "sheet cleanly); found " + ", ".join(f"{k}={v}" for k, v in sorted(papers.items())))
    out = project.out / "readability.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(["FAIL " + x for x in fails] + ["WARN " + x for x in warns]) + "\n", encoding="utf-8")
    if fails:
        return Result("readability", FAIL, f"{len(fails)} problem(s) in {n_done} sheet(s) (out/readability.txt)",
                      fails + ["WARN " + w for w in warns])
    if warns:
        return Result("readability", WARN, f"{n_done} sheet(s) readable, {len(warns)} warning(s)", warns)
    return Result("readability", PASS, f"{n_done} sheet(s): wired like datasheet figures, no overlaps, on grid")
