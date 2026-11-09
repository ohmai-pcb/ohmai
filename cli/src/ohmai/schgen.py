# SPDX-License-Identifier: Apache-2.0
"""`ohmai schematic build`: draw KiCad 9 hierarchical sheets from placement files, datasheet-figure style.

Input: hardware/gen/layout/_root.yaml (sheet order, title) and one hardware/gen/layout/<sheet>.yaml per
functional block:

    title: "4 thermocouple front end"
    paper: A3
    power: {GND: power:GND, 3V3: power:+3V3}      # nets drawn as power symbols (value = net name)
    parts:                                          # every part of the block, official library symbols only
      U5: {lib: Sensor_Temperature:MAX31856, value: MAX31856, mpn: MAX31856MUD+T, nets: {"1": GND, ...}}
      C12: {lib: Device:C, value: 100n, fp: Capacitor_SMD:C_0402_1005Metric, nets: {"1": 3V3, "2": GND}}
    place:
      - {ref: U5, at: [160.02, 101.6]}                       # absolute (mm, snapped to 1.27)
      - {ref: C12, to: U5.5, at: [-7.62, -7.62], orient: v}  # attach: wire from U5 pin 5 to C12's pin on
                                                             # that net, which lands at pin + at
      - {ref: C13, to: C12.1, at: [5.08, 0], orient: v}      # chain on the next part (rail)
      - {ref: Y1, to: U1.21, at: [-10.16, 0], pin: "1", rot: 0}   # multi-pin part: named pin lands there
    connect: [[U1.60, U2.1]]          # extra wires between two pins (L-route, optional 'route' waypoints)
    flags: [{net: VSYS, to: C20.1}]   # PWR_FLAG on a point (ERC: power input driven)

Every IC/passive pin connection inside a block is a real orthogonal wire. Pins whose net is a power net
get a power symbol; nets used by another block get a hierarchical label; anything else gets a local
label, which `ohmai check readability` rejects unless allow-listed (long buses). Library symbols are
copied verbatim from the pinned official KiCad library (ohmai.yaml kicad.libraries); never edited.
"""

from __future__ import annotations

import re
import uuid
from collections import OrderedDict, defaultdict
from pathlib import Path

import yaml

from ohmai import kicadlibs
from ohmai.project import Project
from ohmai.schgeo import body_box, field_local, parse_libsym, pin_out, pin_point, snap, xform
from ohmai.sexp import parse

NS = uuid.UUID("6b1f3c2e-8d4a-4f7e-9c11-0a5e7d2b9f00")
STUB = 2.54
PAPER = {"A4": (297, 210), "A3": (420, 297), "A2": (594, 420)}
GROUND_RE = re.compile(r"^(GND|VSS|AGND|DGND|PGND|GNDA|GNDD|GNDPWR)", re.I)


def U(*k) -> str:
    return str(uuid.uuid5(NS, "/".join(map(str, k))))


def q(v: float) -> str:
    return f"{round(v, 4):g}"


# ---------------------------------------------------------------- library access (verbatim text)
class Lib:
    def __init__(self, sym_dir: Path, project_dir: Path | None = None):
        self.dir = sym_dir
        self.project_dir = project_dir  # hardware/lib: symbols imported by `ohmai kicad-libs fetch` (vetted)
        self._text: dict[str, str] = {}

    def _t(self, lib: str) -> str:
        if lib not in self._text:
            f = self.dir / f"{lib}.kicad_sym"
            if not f.is_file() and self.project_dir is not None:
                f = self.project_dir / f"{lib}.kicad_sym"
            if not f.is_file():
                raise SystemExit(f"schematic build: library {lib} not in {self.dir}"
                                 + (f" or {self.project_dir}" if self.project_dir else "")
                                 + " (missing part: ohmai kicad-libs fetch <MPN>)")
            self._text[lib] = f.read_text(encoding="utf-8")
        return self._text[lib]

    @staticmethod
    def _block(t: str, start: int) -> str:
        depth, j, instr = 0, start, False
        while True:
            ch = t[j]
            if instr:
                if ch == "\\":
                    j += 1
                elif ch == '"':
                    instr = False
            elif ch == '"':
                instr = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return t[start:j + 1]
            j += 1

    def raw(self, lib: str, name: str) -> str:
        t = self._t(lib)
        m = re.search(r'\n[ \t]*(\(symbol "' + re.escape(name) + r'"\s*\n)', t)
        if not m:
            raise SystemExit(f"schematic build: symbol {lib}:{name} not in library {lib}")
        return self._block(t, m.start() + 1)  # keep the line's own indent (golden-stable)

    # KiCad 9 MANDATORY_FIELDS (template_fieldnames.h)
    MANDATORY = ("Reference", "Value", "Footprint", "Datasheet", "Description")
    _PROP = re.compile(r'\n[ \t]+\(property "([^"]+)" "((?:[^"\\]|\\.)*)"')

    def flat(self, lib: str, name: str) -> str:
        """Library text of `name` with `extends` resolved the way LIB_SYMBOL::Flatten() (KiCad 9) does it,
        so the sheet copy matches the library and ERC reports no lib_symbol_mismatch:
        - the parent's units/graphics/pins (recursively flattened), renamed to the child;
        - a mandatory field the child leaves EMPTY keeps the parent's whole field (position, effects);
        - any other child field replaces the parent's field of that name, or is added if the parent lacks it."""
        t = self.raw(lib, name)
        m = re.search(r'\(extends "([^"]+)"\)', t[:500])
        if not m:
            return t
        par = m.group(1)
        p = self.flat(lib, par)
        child = [(mm.group(1), mm.group(2), self._block(t, mm.start() + 1)) for mm in self._PROP.finditer(t)]
        parent = {mm.group(1): self._block(p, mm.start() + 1) for mm in self._PROP.finditer(p)}
        extra = []
        for k, val, blk in child:
            if k in self.MANDATORY and val == "" and k in parent:
                continue
            if k in parent:
                p = p.replace(parent[k], blk, 1)
            else:
                extra.append(blk)
        if extra:
            ends = [mm.start() + 1 + len(self._block(p, mm.start() + 1)) for mm in self._PROP.finditer(p)]
            last = max(ends) if ends else p.index("\n")
            p = p[:last] + "".join(extra) + p[last:]
        p = p.replace(f'(symbol "{par}_', f'(symbol "{name}_').replace(f'(symbol "{par}"', f'(symbol "{name}"', 1)
        return p

    def embedded(self, lib_id: str) -> str:
        """Flattened copy for lib_symbols (what eeschema writes when placing the symbol)."""
        lib, name = lib_id.split(":", 1)
        t = self.flat(lib, name)
        t = t.replace(f'(symbol "{name}"', f'(symbol "{lib_id}"', 1)
        return "\n".join("\t" + ln for ln in t.split("\n"))

    def sym(self, lib_id: str):
        return parse_libsym(parse(self.embedded(lib_id).strip()))


# ---------------------------------------------------------------- union-find on points
class UF:
    def __init__(self):
        self.p: dict = {}

    def f(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def u(self, a, b):
        self.p[self.f(a)] = self.f(b)


def _pt(x, y):
    return (round(x, 3), round(y, 3))


class Sheet:
    def __init__(self, name: str, spec: dict, lib: Lib, ports: set[str], path: str, project: str, pwr_counter):
        self.name, self.spec, self.lib, self.ports, self.path, self.project = name, spec, lib, ports, path, project
        self.parts: dict = spec.get("parts") or {}
        self.power: dict = spec.get("power") or {}
        self.syms: dict = {}
        self.pos: dict = {}
        self.out: list[str] = []
        self.wires: list = []
        self.uf = UF()
        self.done: set = set()        # (ref, pin) connection points handled
        self.netcomp_has: dict = defaultdict(list)  # net -> points whose wire component carries a label/symbol
        self.pwr = pwr_counter
        self.errors: list[str] = []
        self.used_libs: set[str] = set()
        self.mirror: dict = {}
        self.extra_pins: list = []  # power symbol / flag pin points (junction counting)

    # ---- helpers
    def libsym(self, lib_id):
        if lib_id not in self.syms:
            self.syms[lib_id] = self.lib.sym(lib_id)
        self.used_libs.add(lib_id)
        return self.syms[lib_id]

    def groups(self, ref):
        """{pin number: (point, out, [stacked pin numbers])} for a placed part."""
        p = self.parts[ref]
        x, y, rot = self.pos[ref]
        mir = self.mirror.get(ref)
        ls = self.libsym(p["lib"])
        g: dict = OrderedDict()
        bypt: dict = {}
        for pin in ls.pins:
            pt = pin_point(x, y, rot, pin, mir)
            key = _pt(*pt)
            if key in bypt:
                bypt[key][2].append(pin.num)
                g[pin.num] = bypt[key]
            else:
                bypt[key] = (pt, pin_out(rot, pin, mir), [pin.num])
                g[pin.num] = bypt[key]
        return g

    def net_of(self, ref, num):
        return str((self.parts[ref].get("nets") or {}).get(str(num), "")) or None

    def wire(self, a, b):
        if _pt(*a) == _pt(*b):
            return
        if abs(a[0] - b[0]) > 1e-6 and abs(a[1] - b[1]) > 1e-6:
            raise SystemExit(f"{self.name}: non-orthogonal wire {a} -> {b}")
        self.wires.append((a, b))
        self.uf.u(_pt(*a), _pt(*b))

    def route(self, a, out_a, b, waypoints=None):
        pts = [a, *(waypoints or []), b]
        if not waypoints:
            if abs(a[0] - b[0]) > 1e-6 and abs(a[1] - b[1]) > 1e-6:
                corner = (a[0], b[1]) if out_a[0] == 0 else (b[0], a[1])
                pts = [a, corner, b]
        for p0, p1 in zip(pts, pts[1:], strict=False):
            if abs(p0[0] - p1[0]) > 1e-6 and abs(p0[1] - p1[1]) > 1e-6:
                c = (p0[0], p1[1])
                self.wire(p0, c)
                self.wire(c, p1)
            else:
                self.wire(p0, p1)

    # ---- placement
    def place_symbol(self, ref, x, y, rot, mirror=None):
        self.pos[ref] = (snap(x), snap(y), int(rot) % 360)
        if mirror:
            self.mirror[ref] = mirror

    def resolve_point(self, spec_to):
        ref, num = spec_to.split(".", 1)
        if ref not in self.pos:
            raise SystemExit(f"{self.name}: '{spec_to}': {ref} is not placed yet (order the place list)")
        g = self.groups(ref)
        if num not in g:
            raise SystemExit(f"{self.name}: '{spec_to}': no pin {num}")
        return g[num][0], g[num][1], ref, num

    def on_wire(self, P):
        k = _pt(*P)
        for a, b in self.wires:
            a, b = _pt(*a), _pt(*b)
            if (min(a[0], b[0]) - 1e-6 <= k[0] <= max(a[0], b[0]) + 1e-6 and
                    min(a[1], b[1]) - 1e-6 <= k[1] <= max(a[1], b[1]) + 1e-6):
                return True
        return False

    def do_wire(self, it):
        a, b = it["wire"][:2]
        P, oP, ra, na = self.resolve_point(a)
        Q, _oQ, rb, nb = self.resolve_point(b)
        if self.net_of(ra, na) != self.net_of(rb, nb):
            raise SystemExit(f"{self.name}: wire {a}-{b}: different nets")
        wp = [(snap(P[0] + x), snap(P[1] + y)) for x, y in it.get("route", [])]
        self.route(P, oP, Q, wp)
        self.done |= {(ra, na), (rb, nb)}

    def do_tap(self, it):
        """wire from a pin to a point on an existing wire of the same net: {tap: R6.1, at: [0, 5.08]}"""
        P, oP, r, n = self.resolve_point(it["tap"])
        T = (snap(P[0] + it["at"][0]), snap(P[1] + it["at"][1]))
        if not self.on_wire(T):
            raise SystemExit(f"{self.name}: tap {it['tap']}: end {T} is not on a wire")
        wp = [(snap(P[0] + x), snap(P[1] + y)) for x, y in it.get("route", [])]
        self.route(P, oP, T, wp)
        self.done.add((r, n))

    def do_place(self, it):
        if "wire" in it:
            return self.do_wire(it)
        if "tap" in it:
            return self.do_tap(it)
        ref = it["ref"]
        p = self.parts.get(ref)
        if p is None:
            raise SystemExit(f"{self.name}: place {ref}: not in parts")
        ls = self.libsym(p["lib"])
        if "to" not in it:
            x, y = it["at"]
            self.place_symbol(ref, x, y, it.get("rot", 0), it.get("mirror"))
            return
        P, outP, aref, anum = self.resolve_point(it["to"])
        anet = self.net_of(aref, anum)
        if "from" in it:  # start on an existing wire of that net (T-junction), not at the pin itself
            fx, fy = it["from"]
            P = (snap(P[0] + fx), snap(P[1] + fy))
            if not self.on_wire(P):
                raise SystemExit(f"{self.name}: {ref}: 'from' point {P} is not on a wire")
        dx, dy = it.get("at", [0, 0])
        T = (snap(P[0] + dx), snap(P[1] + dy))
        near = str(it["pin"]) if "pin" in it else next(
            (pn.num for pn in ls.pins if self.net_of(ref, pn.num) == anet), None)
        if near is None:
            raise SystemExit(f"{self.name}: {ref} has no pin on {anet} (net of {it['to']})")
        if "orient" in it:
            want = {"v": ("top", "bottom"), "h": ("left", "right")}[it["orient"]]
            side = it.get("near") or (
                (want[0] if dy > 0 or (dy == 0 and outP[1] > 0) else want[1]) if it["orient"] == "v" else
                (want[0] if dx > 0 or (dx == 0 and outP[0] > 0) else want[1]))
            vec = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}[side]
            pin = next(pn for pn in ls.pins if pn.num == near)
            rot = next(r for r in (0, 90, 180, 270) if pin_out(r, pin) == vec)
        else:
            rot = int(it.get("rot", 0))
            pin = next(pn for pn in ls.pins if pn.num == near)
        mir = it.get("mirror")
        ox, oy = xform(rot, pin.x, pin.y, mir)
        self.place_symbol(ref, T[0] - ox, T[1] - oy, rot, mir)
        wp = [(snap(P[0] + a), snap(P[1] + b)) for a, b in it.get("route", [])]
        if "first" in it:  # which axis the wire leaves on: x or y (default: along the pin, or x from a wire tap)
            outP = (1, 0) if it["first"] == "x" else (0, 1)
        elif "from" in it:
            outP = (1, 0) if dx else (0, 1)
        self.route(P, outP, T, wp)
        self.done.add((aref, anum))
        self.done.add((ref, near))

    # ---- emit
    def power_symbol(self, net, at, out, k):
        lib_id = self.power[net]
        ls = self.libsym(lib_id)
        ground = GROUND_RE.match(net) is not None or "GND" in lib_id.upper()
        body_down = ground
        sideways = out[1] == 0 and bool(self.spec.get("sideways_power"))
        if sideways:  # connector style: the symbol points along the stub (rails on a pin header)
            end = (snap(at[0] + out[0] * STUB), snap(at[1] + out[1] * STUB))
            self.wire(at, end)
            rot = (270 if out[0] < 0 else 90) if ground else (90 if out[0] < 0 else 270)
        else:
            # the stub keeps the symbol off the pin unless the pin already points where the body goes
            aligned = (out == (0, 1) and body_down) or (out == (0, -1) and not body_down)
            if aligned and k and k[0] in self.parts:  # short pins (LED, diode): keep the symbol off the body
                ols = self.libsym(self.parts[k[0]]["lib"])
                ox_, oy_, orot = self.pos[k[0]]
                bb = body_box(ols, ox_, oy_, orot, self.mirror.get(k[0]))
                if bb:  # does the part's own body reach past the pin end (LED arrows, diode bars)?
                    reach = (bb[3] - at[1]) if out == (0, 1) else (at[1] - bb[1])
                    aligned = reach < -1.27
            end = at if aligned else (snap(at[0] + out[0] * STUB), snap(at[1] + out[1] * STUB))
            if not aligned:
                self.wire(at, end)
            flip = (out == (0, -1) and body_down) or (out == (0, 1) and not body_down)
            rot = 180 if flip else 0
        self.pwr[0] += 1
        self.extra_pins.append(end)
        ref = f"#PWR{self.pwr[0]:03d}"
        vx, vy, _a, _h = ls.props.get("Value", (0, 0, 0, False))
        tx, ty = xform(rot, vx, vy)
        vang, vjust = field_local(rot, None)
        if sideways:
            tx, ty = out[0] * 3.175, 0
            vang, vjust = field_local(rot, out)
        self.out.append(self._inst(ref, lib_id, net, end[0], end[1], rot, fields=[
            ("Reference", ref, end[0], end[1], True, ""), ("Value", net, end[0] + tx, end[1] + ty, False, vjust, vang),
            ("Footprint", "", end[0], end[1], True, ""), ("Datasheet", "", end[0], end[1], True, "")],
            in_bom=False, on_board=False, pins=[pn.num for pn in ls.pins]))
        return end

    def label(self, net, at, out, kind):
        end = (snap(at[0] + out[0] * STUB), snap(at[1] + out[1] * STUB))
        self.wire(at, end)
        ang = {(1, 0): 0, (-1, 0): 180, (0, -1): 90, (0, 1): 270}[out]
        if kind == "hier":
            just = "left" if ang in (0, 90) else "right"
            self.out.append(f'\t(hierarchical_label "{net}" (shape passive) (at {q(end[0])} {q(end[1])} {ang}) '
                            f'(effects (font (size 1.27 1.27)) (justify {just})) (uuid "{U(self.name, "hl", net, end)}"))')
        else:
            just = "left bottom" if ang in (0, 90) else "right bottom"
            self.out.append(f'\t(label "{net}" (at {q(end[0])} {q(end[1])} {ang}) (effects (font (size 1.27 1.27)) '
                            f'(justify {just})) (uuid "{U(self.name, "l", net, end)}"))')

    def _inst(self, ref, lib_id, value, x, y, rot, fields, in_bom=True, on_board=True, dnp=False, pins=(), mirror=None):
        m = f" (mirror {mirror})" if mirror else ""
        o = [f'\t(symbol (lib_id "{lib_id}") (at {q(x)} {q(y)} {rot}){m} (unit 1) (exclude_from_sim no) '
             f'(in_bom {"yes" if in_bom else "no"}) (on_board {"yes" if on_board else "no"}) (dnp {"yes" if dnp else "no"}) '
             f'(uuid "{U(self.name, "s", ref)}")']
        for fld in fields:
            n, v, fx, fy, hid, just = fld[:6]
            ang = fld[6] if len(fld) > 6 else 0
            j = f" (justify {just})" if just else ""
            h = " (hide yes)" if hid else ""
            o.append(f'\t\t(property "{n}" "{v}" (at {q(snap(fx, 0.635))} {q(snap(fy, 0.635))} {ang}) '
                     f'(effects (font (size 1.27 1.27)){j}{h}))')
        for num in sorted(set(pins), key=str):
            o.append(f'\t\t(pin "{num}" (uuid "{U(self.name, "p", ref, num)}"))')
        o.append(f'\t\t(instances (project "{self.project}" (path "{self.path}" (reference "{ref}") (unit 1))))\n\t)')
        return "\n".join(o)

    def emit_part(self, ref, it):
        p = self.parts[ref]
        x, y, rot = self.pos[ref]
        ls = self.libsym(p["lib"])
        mir = self.mirror.get(ref)
        full = body_box(ls, x, y, rot, mir, with_pins=True) or (x, y, x, y)
        body = body_box(ls, x, y, rot, mir) or full
        two = len({pn.num for pn in ls.pins}) <= 2 and not ls.power
        side = it.get("text")  # right | left | above | below (default from shape)
        if side is None:
            pp = [pin_point(x, y, rot, pn, mir) for pn in ls.pins]
            vertical = two and len(pp) == 2 and abs(pp[0][0] - pp[1][0]) < 1e-6
            side = "right" if vertical else "above" if two else "ic"
        cy, cx = (body[1] + body[3]) / 2, (body[0] + body[2]) / 2
        if side == "right":
            rx, ry, vx, vy, want = body[2] + 0.635, cy - 1.27, body[2] + 0.635, cy + 1.27, (1, 0)
        elif side == "left":
            rx, ry, vx, vy, want = body[0] - 0.635, cy - 1.27, body[0] - 0.635, cy + 1.27, (-1, 0)
        elif side in ("above", "below"):  # one text row: "R10 5.1k" centred over/under the body
            ty = body[1] - 1.27 if side == "above" else body[3] + 1.27
            rx, ry, vx, vy, want = cx - 0.635, ty, cx + 0.635, ty, "pair"
        elif side in ("above2", "below2"):  # two rows
            if side == "above2":
                rx, ry, vx, vy, want = cx, body[1] - 3.175, cx, body[1] - 1.27, None
            else:
                rx, ry, vx, vy, want = cx, body[3] + 1.27, cx, body[3] + 3.175, None
        elif side == "split":  # horizontal part: ref above, value below
            rx, ry, vx, vy, want = cx, body[1] - 1.27, cx, body[3] + 1.27, None
        else:  # ICs, connectors: the library's own field spots (drawn clear of the pins by the librarians)
            want = "lib"
            r0, v0 = ls.props.get("Reference", (0, 0, 0, False)), ls.props.get("Value", (0, 0, 0, False))
            tx, ty = xform(rot, r0[0], r0[1], mir)
            rx, ry = x + tx, y + ty
            tx, ty = xform(rot, v0[0], v0[1], mir)
            vx, vy = x + tx, y + ty
        if "ref_at" in it or "value_at" in it:  # explicit spots: horizontal, reading right from the spot
            want = (1, 0)
            if "ref_at" in it:
                rx, ry = x + it["ref_at"][0], y + it["ref_at"][1]
            if "value_at" in it:
                vx, vy = x + it["value_at"][0], y + it["value_at"][1]
        if want == "lib" and rot in (90, 270):  # keep texts horizontal on rotated symbols
            want = (1, 0)
        if want == "lib":
            ang, just = ls.props.get("Reference", (0, 0, 0))[2], ls.justify.get("Reference", "")
            vang, vjust = ls.props.get("Value", (0, 0, 0))[2], ls.justify.get("Value", "")
        elif want == "pair":
            ang, just = field_local(rot, (-1, 0), mir)
            vang, vjust = field_local(rot, (1, 0), mir)
            if it.get("hide_value"):
                rx, (ang, just) = (rx + vx) / 2, field_local(rot, None, mir)
        else:
            ang, just = field_local(rot, want, mir)
            vang, vjust = ang, just
        fp = p.get("fp", "")
        fields = [("Reference", ref, rx, ry, False, just, ang), ("Value", p.get("value", ""), vx, vy, bool(it.get("hide_value")), vjust, vang),
                  ("Footprint", fp, x, y, True, ""), ("Datasheet", p.get("ds", ""), x, y, True, ""),
                  ("Description", p.get("desc", ""), x, y, True, "")]
        if p.get("mpn"):
            fields.append(("MPN", p["mpn"], x, y, True, ""))
        self.out.append(self._inst(ref, p["lib"], p.get("value", ""), x, y, rot, fields,
                                   in_bom=p.get("bom", True), on_board=p.get("board", True), dnp=p.get("dnp", False),
                                   pins=[pn.num for pn in ls.pins], mirror=self.mirror.get(ref)))

    def occupied(self, pt):
        """Directions in which a wire leaves pt."""
        k = _pt(*pt)
        dirs = set()
        for a, b in self.wires:
            a, b = _pt(*a), _pt(*b)
            for p0, p1 in ((a, b), (b, a)):
                if p0 == k:
                    dirs.add((int((p1[0] > p0[0]) - (p1[0] < p0[0])), int((p1[1] > p0[1]) - (p1[1] < p0[1]))))
            if k not in (a, b) and min(a[0], b[0]) < k[0] < max(a[0], b[0]) and abs(a[1] - k[1]) < 1e-6:
                dirs |= {(1, 0), (-1, 0)}
            if k not in (a, b) and min(a[1], b[1]) < k[1] < max(a[1], b[1]) and abs(a[0] - k[0]) < 1e-6:
                dirs |= {(0, 1), (0, -1)}
        return dirs

    def free_dir(self, pt, out, net):
        occ = self.occupied(pt)
        if out not in occ:
            return out
        body = (-out[0], -out[1])
        if net in self.power:
            ground = GROUND_RE.match(net) is not None or "GND" in self.power[net].upper()
            order = [(0, 1), (1, 0), (-1, 0), (0, -1)] if ground else [(0, -1), (1, 0), (-1, 0), (0, 1)]
        else:
            order = [(1, 0), (-1, 0), (0, -1), (0, 1)]
        for d in order:
            if d not in occ and d != body:
                return d
        return None

    def _join_tees(self):
        """Pins and wire ends on another wire's interior are connected (KiCad does the same)."""
        pts = {_pt(*p) for w in self.wires for p in w} | {_pt(*p) for p in self.extra_pins}
        pts |= {k for v in self.netcomp_has.values() for k in v}
        for ref in self.pos:
            for _num, (pt, _out, _st) in self.groups(ref).items():
                pts.add(_pt(*pt))
        for k in pts:
            self.uf.f(k)
            for a, b in self.wires:
                a, b = _pt(*a), _pt(*b)
                if (min(a[0], b[0]) - 1e-6 <= k[0] <= max(a[0], b[0]) + 1e-6 and
                        min(a[1], b[1]) - 1e-6 <= k[1] <= max(a[1], b[1]) + 1e-6):
                    self.uf.u(k, a)

    def finish_pins(self):
        self._join_tees()
        explicit = self.spec.get("symbols") or []
        for it in explicit:  # author-chosen spots for power symbols / ports on wired rails
            P, oP, r, n = self.resolve_point(it["to"])
            net = self.net_of(r, n)
            if "from" in it:  # a spot on the wire, away from pins and corners
                P = (snap(P[0] + it["from"][0]), snap(P[1] + it["from"][1]))
                if not self.on_wire(P):
                    raise SystemExit(f"{self.name}: symbols {it['to']}: 'from' point {P} is not on a wire")
            if it.get("route"):  # walk a few grid steps first (e.g. to fan two ports apart)
                wp = [(snap(P[0] + x), snap(P[1] + y)) for x, y in it["route"]]
                self.route(P, oP, wp[-1], wp[:-1])
                P = wp[-1]
            if it.get("side") == "on":  # net-name label sitting on the wire itself (no stub, no junction)
                if net in self.power or net in self.ports:
                    raise SystemExit(f"{self.name}: symbols {it['to']}: side 'on' is for local net names only")
                if not self.on_wire(P):
                    raise SystemExit(f"{self.name}: symbols {it['to']}: side 'on' point {P} is not on a wire")
                self.out.append(f'\t(label "{net}" (at {q(P[0])} {q(P[1])} 0) (effects (font (size 1.27 1.27)) '
                                f'(justify left bottom)) (uuid "{U(self.name, "l", net, P)}"))')
                self.netcomp_has[net].append(_pt(*P))
                continue
            d = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[it.get("side", "up")]
            if net in self.power:
                self.power_symbol(net, P, d, (r, n))
            else:
                self.label(net, P, d, "hier" if net in self.ports else "local")
            self._join_tees()
            self.netcomp_has[net].append(_pt(*P))
        todo = []
        for ref in self.pos:
            seen = set()
            for num, (pt, out, stack) in self.groups(ref).items():
                if stack[0] in seen:
                    continue
                seen.add(stack[0])
                nets = {self.net_of(ref, n) for n in stack}
                if len(nets) != 1 or None in nets:
                    self.errors.append(f"{self.name}: {ref} pins {stack}: nets {nets} (every pin needs one net; NC for no-connect)")
                    continue
                net = nets.pop()
                if net == "NC":
                    self.out.append(f'\t(no_connect (at {q(pt[0])} {q(pt[1])}) (uuid "{U(self.name, "nc", ref, num)}"))')
                    continue
                wired = any((ref, n) in self.done for n in stack)
                todo.append((wired, ref, num, pt, out, net))
        todo.sort(key=lambda t: t[0])  # free pins first: symbols/labels go where nothing is wired
        for _wired, ref, num, pt, out, net in todo:
            comp = self.uf.f(_pt(*pt))
            if any(self.uf.f(k) == comp for k in self.netcomp_has[net]):
                continue
            if net not in self.power and net not in self.ports and self._net_pins_in_comp(net, comp):
                self.netcomp_has[net].append(_pt(*pt))
                continue
            d = self.free_dir(pt, out, net)
            if d is None:
                self.errors.append(f"{self.name}: {ref}.{num} ({net}): no free direction for a symbol/label; add a 'symbols' entry")
                continue
            if net in self.power:
                self.power_symbol(net, pt, d, (ref, num))
            elif net in self.ports:
                self.label(net, pt, d, "hier")
            else:
                self.label(net, pt, d, "local")
            self._join_tees()
            self.netcomp_has[net].append(_pt(*pt))

    def _net_pins_in_comp(self, net, comp):
        """True if every pin of this local net in the sheet is in one wire component (no label needed)."""
        pts = [pt for r in self.pos for n, (pt, _o, st) in self.groups(r).items()
               if n == st[0] and self.net_of(r, n) == net]
        return len(pts) > 1 and all(self.uf.f(_pt(*x)) == comp for x in pts)

    def emit_wires(self):
        """KiCad joins a pin or wire end to a wire only at the wire's end points: split every wire at
        the pins, wire ends and symbol pins lying on it (what eeschema does when you draw)."""
        pts = {_pt(*p) for w in self.wires for p in w} | {_pt(*p) for p in self.extra_pins}
        for ref in self.pos:
            for _n, (pt, _o, _s) in self.groups(ref).items():
                pts.add(_pt(*pt))
        n = 0
        for a, b in self.wires:
            a, b = _pt(*a), _pt(*b)
            inner = [p for p in pts if p not in (a, b) and
                     min(a[0], b[0]) - 1e-6 <= p[0] <= max(a[0], b[0]) + 1e-6 and
                     min(a[1], b[1]) - 1e-6 <= p[1] <= max(a[1], b[1]) + 1e-6]
            seq = sorted([a, *inner, b], key=lambda p: abs(p[0] - a[0]) + abs(p[1] - a[1]))
            for p0, p1 in zip(seq, seq[1:], strict=False):
                n += 1
                self.out.append(f'\t(wire (pts (xy {q(p0[0])} {q(p0[1])}) (xy {q(p1[0])} {q(p1[1])})) (stroke (width 0) (type default)) '
                                f'(uuid "{U(self.name, "w", n, p0, p1)}"))')

    def junctions(self):
        pts = defaultdict(int)
        segs = self.wires
        ends = set()
        for a, b in segs:
            ends.add(_pt(*a))
            ends.add(_pt(*b))
        pinpts = defaultdict(int)
        for ref in self.pos:
            for n, (pt, _o, st) in self.groups(ref).items():
                if n == st[0]:
                    pinpts[_pt(*pt)] += 1
        for e in self.extra_pins:
            pinpts[_pt(*e)] += 1
        ends |= set(pinpts)
        for e in ends:
            c = pinpts.get(e, 0)
            for a, b in segs:
                a, b = _pt(*a), _pt(*b)
                if e in (a, b):
                    c += 1
                elif (min(a[0], b[0]) <= e[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= e[1] <= max(a[1], b[1])
                      and (abs(a[0] - b[0]) < 1e-6 or abs(a[1] - b[1]) < 1e-6)):
                    c += 2
            if c >= 3:
                pts[e] = c
        for e in sorted(pts):
            self.out.append(f'\t(junction (at {q(e[0])} {q(e[1])}) (diameter 0) (color 0 0 0 0) (uuid "{U(self.name, "j", e)}"))')

    def build(self, sheet_uuid, title, paper, page):
        for it in self.spec.get("place") or []:
            self.do_place(it)
        missing = sorted(set(self.parts) - set(self.pos))
        if missing:
            raise SystemExit(f"{self.name}: parts not placed: {', '.join(missing)}")
        for a, b in [tuple(c[:2]) for c in self.spec.get("connect") or []]:
            P, oP, ra, na = self.resolve_point(a)
            Q, _oQ, rb, nb = self.resolve_point(b)
            if self.net_of(ra, na) != self.net_of(rb, nb):
                raise SystemExit(f"{self.name}: connect {a}-{b}: different nets")
            self.route(P, oP, Q)
            self.done |= {(ra, na), (rb, nb)}
        for it in self.spec.get("place") or []:
            if "ref" in it:
                self.emit_part(it["ref"], it)
        for fl in self.spec.get("flags") or []:
            P, oP, r, n = self.resolve_point(fl["to"])
            if "from" in fl:
                P = (snap(P[0] + fl["from"][0]), snap(P[1] + fl["from"][1]))
            side = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}[fl.get("side", "up")]
            end = (snap(P[0] + side[0] * STUB), snap(P[1] + side[1] * STUB))
            self.wire(P, end)
            self.pwr[0] += 1
            self.extra_pins.append(end)
            ref = f"#FLG{self.pwr[0]:03d}"
            ls = self.libsym("power:PWR_FLAG")
            rot = {(0, -1): 0, (0, 1): 180, (-1, 0): 90, (1, 0): 270}[side]
            vx, vy, _a, _h = ls.props.get("Value", (0, 0, 0, False))
            tx, ty = xform(rot, vx, vy)
            vang, vjust = field_local(rot, None)
            if side[1] == 0:  # sideways flag: text beyond the symbol, reading away from the wire
                tx, ty = side[0] * 3.175, 0
                vang, vjust = field_local(rot, side)
            self.out.append(self._inst(ref, "power:PWR_FLAG", "PWR_FLAG", end[0], end[1], rot, fields=[
                ("Reference", ref, end[0], end[1], True, ""), ("Value", "PWR_FLAG", end[0] + tx, end[1] + ty, False, vjust, vang),
                ("Footprint", "", end[0], end[1], True, ""), ("Datasheet", "", end[0], end[1], True, "")],
                in_bom=False, on_board=False, pins=[pn.num for pn in ls.pins]))
        self.finish_pins()
        self.emit_wires()
        self.junctions()
        head = [f'(kicad_sch\n\t(version 20250114)\n\t(generator "eeschema")\n\t(generator_version "9.0")\n\t(uuid "{sheet_uuid}")\n\t(paper "{paper}")',
                f'\t(title_block (title "{title}") (rev "A") (company "{self.project}") '
                f'(comment 1 "Drawn by ohmai schematic build from hardware/gen/layout/{self.name}.yaml") '
                f'(comment 2 "Official KiCad library symbols only (ohmai.yaml kicad.libraries)"))',
                "\t(lib_symbols\n" + "\n".join(self.lib.embedded(lb) for lb in sorted(self.used_libs)) + "\n\t)"]
        return "\n".join(head + self.out + ['\t(sheet_instances (path "/" (page "1")))\n\t(embedded_fonts no)\n)']) + "\n"


def build(project: Project, layout_dir: Path | None = None) -> list[Path]:
    tag = kicadlibs.pinned(project)
    if not tag:
        raise SystemExit("ohmai schematic build: pin kicad.libraries in ohmai.yaml (ohmai kicad-libs)")
    sym_dir, _fp = kicadlibs.dirs(tag)
    hw = project.schematic.parent
    lib = Lib(sym_dir, hw / "lib")
    ld = layout_dir or hw / "gen" / "layout"
    root = yaml.safe_load((ld / "_root.yaml").read_text(encoding="utf-8")) or {}
    names = root.get("sheets") or []
    specs = {n: yaml.safe_load((ld / f"{n}.yaml").read_text(encoding="utf-8")) or {} for n in names}
    for s in specs.values():  # one power-symbol map for the whole design (root), sheets may add
        s["power"] = {**(root.get("power") or {}), **(s.get("power") or {})}
    proj = project.config.get("project") or project.root.name
    powers = set().union(*[set((s.get("power") or {}).keys()) for s in specs.values()])
    net_sheets = defaultdict(set)
    for n, s in specs.items():
        for p in (s.get("parts") or {}).values():
            for net in (p.get("nets") or {}).values():
                if net != "NC":
                    net_sheets[str(net)].add(n)
    ports = {n: sorted(x for x, ss in net_sheets.items() if n in ss and len(ss) > 1 and x not in powers) for n in names}
    root_uuid = U(proj, "root")
    pwr = [0]
    written, errors = [], []
    for i, n in enumerate(names):
        s = specs[n]
        suid = U(proj, "sheet", n)
        sh = Sheet(n, s, lib, set(ports[n]), f"/{root_uuid}/{suid}", proj, pwr)
        text = sh.build(U(proj, "file", n), f"{proj}: {s.get('title', n)}", s.get("paper", "A3"), i + 2)
        errors += sh.errors
        f = hw / f"{n}.kicad_sch"
        f.write_text(text, encoding="utf-8")
        written.append(f)
    # root: sheet symbols in a column, ports as sheet pins with stub + label (root is exempt from the
    # local-label rule: it only joins blocks)
    out = [f'(kicad_sch\n\t(version 20250114)\n\t(generator "eeschema")\n\t(generator_version "9.0")\n\t(uuid "{root_uuid}")\n\t(paper "{root.get("paper", "A3")}")',
           f'\t(title_block (title "{root.get("title", proj)}") (rev "A") (company "{proj}"))', "\t(lib_symbols)"]
    x, y = 50.8, 38.1
    W = 45.72
    for i, n in enumerate(names):
        pl = ports[n]
        h = snap((len(pl) + 1) * 2.54, 2.54)
        if y + h > PAPER[root.get("paper", "A3")][1] - 30:
            x, y = x + 127.0, 38.1
        suid = U(proj, "sheet", n)
        out.append(f'\t(sheet (at {q(x)} {q(y)}) (size {q(W)} {q(h)}) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) '
                   f'(fields_autoplaced yes) (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0000)) (uuid "{suid}")')
        out.append(f'\t\t(property "Sheetname" "{n}" (at {q(x)} {q(y - 0.635)} 0) (effects (font (size 1.27 1.27)) (justify left bottom)))')
        out.append(f'\t\t(property "Sheetfile" "{n}.kicad_sch" (at {q(x)} {q(y + h + 0.635)} 0) (effects (font (size 1.27 1.27)) (justify left top)))')
        tail = []
        for j, net in enumerate(pl):
            py = y + 2.54 * (j + 1)
            px = x + W
            out.append(f'\t\t(pin "{net}" passive (at {q(px)} {q(py)} 0) (uuid "{U(proj, "sp", n, net)}") (effects (font (size 1.27 1.27)) (justify right)))')
            tail.append(f'\t(wire (pts (xy {q(px)} {q(py)}) (xy {q(px + STUB)} {q(py)})) (stroke (width 0) (type default)) (uuid "{U(proj, "rw", n, net)}"))')
            tail.append(f'\t(label "{net}" (at {q(px + STUB)} {q(py)} 0) (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "{U(proj, "rl", n, net)}"))')
        out.append(f'\t\t(instances (project "{proj}" (path "/{root_uuid}" (page "{i + 2}"))))\n\t)')
        out += tail
        y += h + 12.7
    out.append('\t(sheet_instances (path "/" (page "1")))\n\t(embedded_fonts no)\n)')
    project.schematic.write_text("\n".join(out) + "\n", encoding="utf-8")
    written.insert(0, project.schematic)
    if errors:
        raise SystemExit("schematic build:\n  " + "\n  ".join(errors))
    return written
