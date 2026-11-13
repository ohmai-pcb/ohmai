# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Schematic geometry shared by `ohmai schematic build` and `ohmai check readability`.

Coordinates are KiCad schematic mm, y down. Library symbols are y up; `xform` maps them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ohmai.sexp import find, first

GRID = 1.27
# symbol rotation -> (a, b, c, d): sx = a*x + b*y, sy = c*x + d*y   (lib y up -> sheet y down)
_TR = {0: (1, 0, 0, -1), 90: (0, -1, -1, 0), 180: (-1, 0, 0, 1), 270: (0, 1, 1, 0)}


def xform(rot: int, x: float, y: float, mirror: str | None = None) -> tuple[float, float]:
    if mirror == "x":
        y = -y
    elif mirror == "y":
        x = -x
    a, b, c, d = _TR[int(rot) % 360]
    return a * x + b * y, c * x + d * y


def on_grid(v: float, g: float = GRID) -> bool:
    return abs(v / g - round(v / g)) < 1e-3


def snap(v: float, g: float = GRID) -> float:
    return round(round(v / g) * g, 4)


@dataclass
class Pin:
    num: str
    name: str
    etype: str
    x: float
    y: float
    ang: float
    length: float
    hidden: bool


@dataclass
class LibSym:
    name: str
    pins: list[Pin] = field(default_factory=list)
    body: tuple[float, float, float, float] | None = None  # lib coords x0,y0,x1,y1 (graphics only)
    props: dict[str, tuple[float, float, float, bool]] = field(default_factory=dict)  # name -> x,y,ang,hidden
    justify: dict[str, str] = field(default_factory=dict)  # name -> "left bottom" ...
    power: bool = False


def _num(v) -> float:
    return float(v)


def parse_libsym(node) -> LibSym:
    """node = (symbol "Lib:Name" ...) as embedded in lib_symbols (already flattened)."""
    ls = LibSym(name=node[1])
    ls.power = any(isinstance(c, list) and c and c[0] == "power" for c in node)
    xs: list[float] = []
    ys: list[float] = []

    def add(x, y):
        xs.append(x)
        ys.append(y)

    for prop in find(node, "property"):
        at = first(prop, "at")
        eff = first(prop, "effects")
        hid = bool(eff and any(isinstance(c, list) and c and c[0] == "hide" and (len(c) < 2 or c[1] == "yes")
                               for c in eff)) or bool(eff and "hide" in eff)
        if at:
            ls.props[prop[1]] = (_num(at[1]), _num(at[2]), _num(at[3]) if len(at) > 3 else 0.0, hid)
            j = first(eff, "justify") if eff else None
            ls.justify[prop[1]] = " ".join(str(v) for v in j[1:]) if j else ""
    for sub in find(node, "symbol"):
        for g in sub:
            if not isinstance(g, list) or not g:
                continue
            if g[0] == "rectangle":
                s, e = first(g, "start"), first(g, "end")
                add(_num(s[1]), _num(s[2]))
                add(_num(e[1]), _num(e[2]))
            elif g[0] in ("polyline", "bezier"):
                for xy in find(first(g, "pts") or [], "xy"):
                    add(_num(xy[1]), _num(xy[2]))
            elif g[0] == "circle":
                c, r = first(g, "center"), _num(first(g, "radius")[1])
                add(_num(c[1]) - r, _num(c[2]) - r)
                add(_num(c[1]) + r, _num(c[2]) + r)
            elif g[0] == "arc":
                for k in ("start", "mid", "end"):
                    p = first(g, k)
                    if p:
                        add(_num(p[1]), _num(p[2]))
            elif g[0] == "pin":
                at, ln = first(g, "at"), first(g, "length")
                hid = any((isinstance(k, list) and k and k[0] == "hide" and (len(k) < 2 or k[1] == "yes"))
                          or k == "hide" for k in g)
                nm, nu = first(g, "name"), first(g, "number")
                ls.pins.append(Pin(nu[1], nm[1], g[1], _num(at[1]), _num(at[2]),
                                   _num(at[3]) if len(at) > 3 else 0.0, _num(ln[1]) if ln else 0.0, hid))
    if xs:
        ls.body = (min(xs), min(ys), max(xs), max(ys))
    return ls


def pin_point(px: float, py: float, rot: int, pin: Pin, mirror=None) -> tuple[float, float]:
    sx, sy = xform(rot, pin.x, pin.y, mirror)
    return round(px + sx, 4), round(py + sy, 4)


def pin_out(rot: int, pin: Pin, mirror=None) -> tuple[int, int]:
    """Unit vector pointing away from the body at the pin's connection point (sheet coords)."""
    ix, iy = math.cos(math.radians(pin.ang)), math.sin(math.radians(pin.ang))
    ox, oy = xform(rot, -ix, -iy, mirror)
    return int(round(ox)), int(round(oy))


def body_box(ls: LibSym, px: float, py: float, rot: int, mirror=None, with_pins: bool = False):
    pts = []
    if ls.body:
        x0, y0, x1, y1 = ls.body
        pts += [(x0, y0), (x1, y1), (x0, y1), (x1, y0)]
    if with_pins:
        for p in ls.pins:
            ix, iy = math.cos(math.radians(p.ang)), math.sin(math.radians(p.ang))
            pts += [(p.x, p.y), (p.x + ix * p.length, p.y + iy * p.length)]
    if not pts:
        return None
    tp = [xform(rot, x, y, mirror) for x, y in pts]
    return (px + min(x for x, _ in tp), py + min(y for _, y in tp), px + max(x for x, _ in tp), py + max(y for _, y in tp))


CHAR_W = 0.78  # average stroke-font advance per character, in units of the text size (estimate)
_WIDE = set("ABCDEFGHKMNOPQRSUVWXYZ_mw%@")  # measured on kicad-cli renders: ~0.95 x size


def text_width(text: str, size: float) -> float:
    """ESTIMATE of the KiCad stroke-font width (uppercase ~0.95, narrow ~0.45, others ~0.75 x size)."""
    w = 0.0
    for ch in text:
        w += 0.95 if ch in _WIDE else 0.45 if ch in "il1.,:;'|!I()[] " else 0.75
    return max(w, 1.0) * size


def text_box(text: str, x: float, y: float, ang: float, size: float, justify: str = "",
             shape_pad: float = 0.0) -> tuple[float, float, float, float]:
    """Approximate bbox of a schematic text. ang = effective display angle."""
    w = text_width(text, size) + shape_pad
    h = size * 1.2
    a = int(round(ang)) % 360
    j = justify.split()
    horiz = "left" if "left" in j else "right" if "right" in j else "center"
    vert = "top" if "top" in j else "bottom" if "bottom" in j else "center"
    # 180/270 are drawn readable as 0/90; the stored justification already says where the text goes
    vertical = a in (90, 270)
    along0 = {"left": 0.0, "right": -w, "center": -w / 2}[horiz]
    across0 = {"top": 0.0, "bottom": -h, "center": -h / 2}[vert]
    if not vertical:
        return (x + along0, y + across0, x + along0 + w, y + across0 + h)
    # vertical text reads bottom-to-top: 'along' runs toward -y
    return (x + across0, y - along0 - w, x + across0 + h, y - along0)


# symbol transform on y-down offsets (KiCad TRANSFORM): x' = a*x + b*y, y' = c*x + d*y
_TD = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}


def _td(rot, x, y, mirror=None):
    if mirror == "x":
        y = -y
    elif mirror == "y":
        x = -x
    a, b, c, d = _TD[int(rot) % 360]
    return a * x + b * y, c * x + d * y


def field_box(text, x, y, ang, size, justify, rot, mirror=None):
    """Drawn bbox of a symbol field: the file stores the drawn anchor; angle and justification are
    in the symbol's frame and follow its transform (as KiCad's SCH_FIELD::GetBoundingBox)."""
    b = text_box(text, 0.0, 0.0, ang, size, justify)
    pts = [_td(rot, px, py, mirror) for px, py in ((b[0], b[1]), (b[2], b[3]))]
    return (x + min(p[0] for p in pts), y + min(p[1] for p in pts), x + max(p[0] for p in pts), y + max(p[1] for p in pts))


def field_local(rot, want: tuple[int, int] | None, mirror=None):
    """(stored angle, horizontal justify) so a field draws horizontally, extending toward `want`
    ((1,0) = to the right of the anchor, (-1,0) = to the left, None = centred)."""
    inv = {0: 0, 90: 270, 180: 180, 270: 90}[int(rot) % 360]
    if want is None:
        dx, dy = _td(inv, 1, 0)
        return (0 if abs(dx) > 0.5 else 90), ""
    dx, dy = _td(inv, *want)
    if mirror == "y":
        dx = -dx
    elif mirror == "x":
        dy = -dy
    dx, dy = round(dx), round(dy)
    return {(1, 0): (0, "left"), (-1, 0): (0, "right"), (0, -1): (90, "left"), (0, 1): (90, "right")}[(dx, dy)]


def overlap(a, b, shrink: float = 0.0) -> bool:
    return (min(a[2], b[2]) - max(a[0], b[0]) > shrink) and (min(a[3], b[3]) - max(a[1], b[1]) > shrink)


def seg_hits_box(p, q, box, shrink: float = 0.3) -> bool:
    """Axis-aligned segment passes through the interior of box (shrunk)."""
    x0, y0, x1, y1 = box[0] + shrink, box[1] + shrink, box[2] - shrink, box[3] - shrink
    if x0 >= x1 or y0 >= y1:
        return False
    sx0, sx1 = sorted((p[0], q[0]))
    sy0, sy1 = sorted((p[1], q[1]))
    return sx1 > x0 and sx0 < x1 and sy1 > y0 and sy0 < y1


def norm_net(s: str) -> str:
    return re.sub(r"\s+", "", s)
