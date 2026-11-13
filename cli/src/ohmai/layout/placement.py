# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Placement model read straight from a .kicad_pcb (no KiCad needed): footprints, pads, courtyards, vias, tracks.

Geometry is axis-aligned boxes: a pad is the bounding box of its rotated rectangle (exact at 0/90/180/270 degrees,
conservative otherwise; round corners are ignored, so gaps come out a little small, never large). Courtyards are
the bounding box(es) of each disconnected loop/shape on the footprint's F.CrtYd / B.CrtYd layer, so dual-row edge
castellation footprints (e.g. RPi_Pico_Castellated_2x20) model their two edge strips as two separate courtyard
boxes rather than one board-spanning box. Coordinates are KiCad board mm (y down).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from ohmai import sexp

Box = tuple[float, float, float, float]   # x0, y0, x1, y1


@dataclass
class Pad:
    ref: str
    num: str
    x: float
    y: float
    box: Box
    net: str
    layers: tuple[str, ...]
    hole: bool = False
    # Hole diameter mm from (drill …). Oval (drill oval w h): max(w, h).
    drill: float = 0.0
    # Preserve the true pad for routing terminals. A placement AABB is a safe
    # obstacle envelope but NOT copper (round/rotated pads have empty corners).
    shape: str = "rect"
    size: tuple[float, float] | None = None
    rotation: float = 0.0
    roundrect_ratio: float = 0.0
    # Custom pads: KiCad anchor shape ("circle"/"rect") and copper primitives in
    # the pad-local frame (before pad rotation). See `pad_primitives`.
    anchor: str = "circle"
    primitives: tuple = ()

    @property
    def name(self) -> str:
        return f"{self.ref}.{self.num}"

    @property
    def w(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def h(self) -> float:
        return self.box[3] - self.box[1]


@dataclass
class Footprint:
    ref: str
    lib: str
    layer: str           # F.Cu / B.Cu
    x: float
    y: float
    rot: float
    pads: list[Pad] = field(default_factory=list)
    courtyard: Box | None = None
    local_cy: list[tuple[float, float]] = field(default_factory=list)   # courtyard points, footprint frame
    courtyards: list[Box] = field(default_factory=list)                 # per-island world boxes
    local_cys: list[list[tuple[float, float]]] = field(default_factory=list)  # per-island points, footprint frame

    def __post_init__(self) -> None:
        if self.local_cy and not self.local_cys:
            self.local_cys = [list(self.local_cy)]
        if self.courtyard is not None and not self.courtyards:
            self.courtyards = [self.courtyard]

    @property
    def side(self) -> str:
        return "B" if self.layer.startswith("B") else "F"


@dataclass
class Via:
    x: float
    y: float
    d: float  # outer diameter (from via size); public meaning unchanged
    net: str
    drill: float = 0.0

    @property
    def size(self) -> float:
        """Alias for outer diameter `d` (handroute / freeze snapshots)."""
        return self.d


@dataclass(frozen=True)
class Track:
    net: str
    layer: str
    x0: float
    y0: float
    x1: float
    y1: float
    width: float


@dataclass(frozen=True)
class Zone:
    """Filled copper zone (pour). `box` is AABB of filled_polygon pts (or outline if unfilled)."""
    net: str
    layer: str
    box: Box
    filled: bool = True
    polygon: tuple[tuple[float, float], ...] | None = None


@dataclass
class Board:
    footprints: dict[str, Footprint]
    vias: list[Via]
    tracks: list[tuple[str, str]]      # (net, layer) per segment / arc
    edge: Box | None
    segments: list[Track] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)

    @property
    def edge_box(self) -> Box | None:
        """Alias for `edge` (fanout / handroute)."""
        return self.edge

    def pads(self, side: str | None = None):
        for fp in self.footprints.values():
            for p in fp.pads:
                if side is None or any(lay.startswith(side + ".") or lay == "*.Cu" for lay in p.layers):
                    yield p


def rotate(x: float, y: float, deg: float) -> tuple[float, float]:
    """KiCad rotation (degrees, counter-clockwise on screen, y down)."""
    if not deg:
        return x, y
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return x * c + y * s, -x * s + y * c


def _f(node, key, n=2, default=None):
    c = sexp.first(node, key)
    if c is None:
        return default
    vals = [float(v) for v in c[1:1 + n] if isinstance(v, str) and _num(v)]
    return vals + [0.0] * (n - len(vals)) if vals else default


def _num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def _drill(node) -> float:
    """Parse KiCad `(drill …)`. Circular: first number. Oval `(drill oval w h)`: max(w, h)."""
    c = sexp.first(node, "drill")
    if c is None:
        return 0.0
    nums = [float(v) for v in c[1:] if isinstance(v, str) and _num(v)]
    if not nums:
        return 0.0
    return max(nums) if len(nums) > 1 else nums[0]


def _rect_box(cx, cy, w, h, deg) -> Box:
    pts = [rotate(dx, dy, deg) for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
    xs = [cx + p[0] for p in pts]
    ys = [cy + p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _pad_box(p, cx, cy, w, h, deg) -> Box:
    """Pad bbox incl. custom-pad primitives (anchor `size` alone can be 0.005 mm)."""
    x0, y0, x1, y1 = _rect_box(cx, cy, w, h, deg)
    prim = sexp.first(p, "primitives")
    if not prim:
        return x0, y0, x1, y1
    pts: list[tuple[float, float]] = []
    for g in prim[1:]:
        if not isinstance(g, list) or not g:
            continue
        wn = _f(g, "width", 1)
        half = (wn[0] if wn else 0.0) / 2
        local: list[tuple[float, float]] = []
        if g[0] == "gr_poly":
            pl = sexp.first(g, "pts")
            for xy in (pl[1:] if pl else []):
                if isinstance(xy, list) and xy and xy[0] == "xy":
                    local.append((float(xy[1]), float(xy[2])))
        elif g[0] in ("gr_rect", "gr_line"):
            a, b = _f(g, "start"), _f(g, "end")
            if a and b:
                local += [tuple(a), tuple(b), (a[0], b[1]), (b[0], a[1])]
        elif g[0] == "gr_circle":
            c, e = _f(g, "center"), _f(g, "end")
            if c and e:
                r = math.hypot(e[0] - c[0], e[1] - c[1])
                local += [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r), (c[0] - r, c[1] + r), (c[0] + r, c[1] - r)]
        for lx, ly in local:
            for ox, oy in ((-half, -half), (half, -half), (half, half), (-half, half)):
                rx, ry = rotate(lx + ox, ly + oy, deg)
                pts.append((cx + rx, cy + ry))
    if pts:
        x0 = min(x0, *(q[0] for q in pts))
        y0 = min(y0, *(q[1] for q in pts))
        x1 = max(x1, *(q[0] for q in pts))
        y1 = max(y1, *(q[1] for q in pts))
    return x0, y0, x1, y1


def _fill_yes(g, default: bool) -> bool:
    f = sexp.first(g, "fill")
    if f is None or len(f) < 2:
        return default
    return str(f[1]) in ("yes", "solid", "true")


def pad_primitives(p) -> tuple:
    """Custom-pad copper primitives in the pad-local frame, unrotated.

    Each entry is a plain tuple so Pad stays hashable/serialisable:
    ("poly", pts, width, filled), ("rect", start, end, width, filled),
    ("circle", center, radius, width, filled), ("line", start, end, width),
    ("arc", start, mid, end, width). Bezier curves are kept as their control
    polyline ("line" chain), which is inside their hull and so never invents copper
    far outside the real curve.
    """
    prim = sexp.first(p, "primitives")
    if not prim:
        return ()
    out = []
    for g in prim[1:]:
        if not isinstance(g, list) or not g:
            continue
        wn = _f(g, "width", 1)
        width = wn[0] if wn else 0.0
        head = g[0]
        if head == "gr_poly":
            pl = sexp.first(g, "pts")
            pts = tuple((float(xy[1]), float(xy[2])) for xy in (pl[1:] if pl else [])
                        if isinstance(xy, list) and xy and xy[0] == "xy")
            if len(pts) >= 3:
                # Pad polygons are filled unless explicitly (fill no/none).
                out.append(("poly", pts, width, _fill_yes(g, True)))
            elif len(pts) == 2:
                out.append(("line", pts[0], pts[1], width))
        elif head == "gr_rect":
            a, b = _f(g, "start"), _f(g, "end")
            if a and b:
                out.append(("rect", tuple(a), tuple(b), width, _fill_yes(g, False)))
        elif head == "gr_circle":
            c, e = _f(g, "center"), _f(g, "end")
            if c and e:
                out.append(("circle", tuple(c), math.hypot(e[0] - c[0], e[1] - c[1]), width, _fill_yes(g, False)))
        elif head == "gr_line":
            a, b = _f(g, "start"), _f(g, "end")
            if a and b:
                out.append(("line", tuple(a), tuple(b), width))
        elif head == "gr_arc":
            a, m, b = _f(g, "start"), _f(g, "mid"), _f(g, "end")
            if a and m and b:
                out.append(("arc", tuple(a), tuple(m), tuple(b), width))
            elif a and b:
                out.append(("line", tuple(a), tuple(b), width))
        elif head in ("gr_curve", "gr_bezier"):
            pl = sexp.first(g, "pts")
            pts = [(float(xy[1]), float(xy[2])) for xy in (pl[1:] if pl else [])
                   if isinstance(xy, list) and xy and xy[0] == "xy"]
            for a, b in zip(pts, pts[1:], strict=False):
                out.append(("line", a, b, width))
    return tuple(out)


def _pad_anchor(p) -> str:
    opts = sexp.first(p, "options")
    anc = sexp.first(opts, "anchor") if opts else None
    return str(anc[1]) if anc and len(anc) > 1 else "circle"


def _graphic_points(g) -> list[tuple[float, float]]:
    head = g[0]
    if head in ("fp_line", "fp_rect"):
        a, b = _f(g, "start"), _f(g, "end")
        if a and b:
            return [tuple(a), tuple(b), (a[0], b[1]), (b[0], a[1])] if head == "fp_rect" else [tuple(a), tuple(b)]
    if head == "fp_circle":
        c, e = _f(g, "center"), _f(g, "end")
        if c and e:
            r = math.dist(c, e)
            return [(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)]
    if head == "fp_arc":
        return [tuple(p) for p in (_f(g, "start"), _f(g, "mid"), _f(g, "end")) if p]
    if head == "fp_poly":
        pts = sexp.first(g, "pts") or []
        return [(float(xy[1]), float(xy[2])) for xy in sexp.find(pts, "xy")]
    return []


def _cy_islands(items: list) -> list[list[tuple[float, float]]]:
    """Group CrtYd graphic items into disconnected courtyard islands in footprint-local coordinates."""
    raw_groups: list[list[tuple[float, float]]] = []
    segs: list[list[tuple[float, float]]] = []
    for g in items:
        pts = _graphic_points(g)
        if not pts:
            continue
        if g[0] in ("fp_rect", "fp_circle", "fp_poly"):
            raw_groups.append(pts)
        else:
            segs.append(pts)
    if segs:
        n = len(segs)
        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(n):
            for j in range(i + 1, n):
                if any(math.hypot(pa[0] - pb[0], pa[1] - pb[1]) <= 0.02 for pa in segs[i] for pb in segs[j]):
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[rj] = ri
        by_root: dict[int, list[tuple[float, float]]] = {}
        for i, pts in enumerate(segs):
            by_root.setdefault(find(i), []).extend(pts)
        raw_groups.extend(by_root.values())
    if len(raw_groups) <= 1:
        return raw_groups

    def _pts_box(pts: list[tuple[float, float]]) -> Box:
        return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)

    # Merge any islands whose local bounding boxes overlap or touch
    merged = [list(g) for g in raw_groups]
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i in range(len(merged)):
            bi = _pts_box(merged[i])
            for j in range(i + 1, len(merged)):
                bj = _pts_box(merged[j])
                if overlap(bi, bj, tol=-1e-6):
                    merged[i].extend(merged[j])
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return merged


def _layer(node) -> str:
    c = sexp.first(node, "layer")
    return c[1] if c and len(c) > 1 else ""


def load(path: Path) -> Board:
    tree = sexp.parse(Path(path).read_text(encoding="utf-8"))
    nets = {c[1]: c[2] for c in sexp.find(tree, "net") if len(c) >= 3}
    fps: dict[str, Footprint] = {}
    for f in sexp.find(tree, "footprint"):
        at = sexp.first(f, "at")
        x, y = float(at[1]), float(at[2])
        rot = float(at[3]) if len(at) > 3 and _num(at[3]) else 0.0
        ref = ""
        for prop in sexp.find(f, "property"):
            if len(prop) > 2 and prop[1] == "Reference":
                ref = prop[2]
        fp_text = [t for t in sexp.find(f, "fp_text") if len(t) > 2 and t[1] == "reference"]
        ref = ref or (fp_text[0][2] if fp_text else f"?{len(fps)}")
        fp = Footprint(ref, f[1] if len(f) > 1 and isinstance(f[1], str) else "", _layer(f), x, y, rot)
        cy_layer = ("B" if fp.side == "B" else "F") + ".CrtYd"
        cy_items = [
            g for g in f
            if isinstance(g, list) and g and g[0].startswith("fp_") and _layer(g) == cy_layer
        ]
        fp.local_cys = _cy_islands(cy_items)
        fp.local_cy = [pt for isl in fp.local_cys for pt in isl]
        if fp.local_cys:
            fp.courtyards = moved_courtyards(fp, x, y, rot)
            fp.courtyard = moved_courtyard(fp, x, y, rot)
        for p in sexp.find(f, "pad"):
            pat = sexp.first(p, "at")
            px, py = float(pat[1]), float(pat[2])
            pdeg = float(pat[3]) if len(pat) > 3 and _num(pat[3]) else 0.0   # absolute angle (KiCad 6+)
            size = _f(p, "size") or [0.0, 0.0]
            dx, dy = rotate(px, py, rot)
            net_n = sexp.first(p, "net")
            net = (net_n[2] if len(net_n) > 2 else nets.get(net_n[1], "")) if net_n else ""
            lay = sexp.first(p, "layers")
            layers = tuple(lay[1:]) if lay else ()
            fp.pads.append(Pad(ref, p[1], x + dx, y + dy, _pad_box(p, x + dx, y + dy, size[0], size[1], pdeg), net,
                               layers, hole=len(p) > 2 and p[2] in ("thru_hole", "np_thru_hole"),
                               drill=_drill(p), shape=p[3] if len(p) > 3 else "rect",
                               size=tuple(size), rotation=pdeg,
                               roundrect_ratio=(_f(p, "roundrect_rratio", 1) or [0.0])[0],
                               anchor=_pad_anchor(p), primitives=pad_primitives(p)))
        fps[ref] = fp
    vias = []
    for v in sexp.find(tree, "via"):
        at, sz, n = sexp.first(v, "at"), sexp.first(v, "size"), sexp.first(v, "net")
        net = (n[2] if len(n) > 2 else nets.get(n[1], "")) if n else ""
        vias.append(Via(float(at[1]), float(at[2]), float(sz[1]) if sz else 0.0, net, drill=_drill(v)))
    tracks = []
    segments: list[Track] = []
    for t in sexp.find(tree, "segment") + sexp.find(tree, "arc"):
        n = sexp.first(t, "net")
        tnet = (n[2] if len(n) > 2 else nets.get(n[1], "")) if n else ""
        tlay = _layer(t)
        tracks.append((tnet, tlay))
        a, b = _f(t, "start"), _f(t, "end")
        w = _f(t, "width", n=1)
        if a and b:
            segments.append(Track(tnet, tlay, a[0], a[1], b[0], b[1], w[0] if w else 0.0))
    pts = []
    for g in tree:
        if isinstance(g, list) and g and g[0].startswith("gr_") and _layer(g) == "Edge.Cuts":
            pts += _graphic_points(["fp" + g[0][2:], *g[1:]])
    edge = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)) if pts else None
    zones: list[Zone] = []
    for z in sexp.find(tree, "zone"):
        nn = sexp.first(z, "net_name")
        net = nn[1] if nn and len(nn) > 1 and isinstance(nn[1], str) else ""
        if not net:
            n = sexp.first(z, "net")
            if n and len(n) > 2 and isinstance(n[2], str):
                net = n[2]
            elif n and len(n) > 1:
                net = nets.get(n[1], "")
        lay_n = sexp.first(z, "layer")
        if lay_n and len(lay_n) > 1:
            zlayers = [lay_n[1]]
        else:
            lays = sexp.first(z, "layers")
            zlayers = list(lays[1:]) if lays else []
        # Prefer filled copper; fall back to zone outline polygon (unfilled keepout-ish).
        filled_nodes = [c for c in z[1:] if isinstance(c, list) and c and c[0] == "filled_polygon"]
        outline_nodes = [c for c in z[1:] if isinstance(c, list) and c and c[0] == "polygon"]
        src_nodes = filled_nodes or outline_nodes
        filled = bool(filled_nodes)
        for node in src_nodes:
            # filled_polygon may carry (layer ...); else use zone layer list.
            nlay = _layer(node) if sexp.first(node, "layer") else None
            use_layers = [nlay] if nlay else zlayers
            pl = sexp.first(node, "pts")
            xys = []
            for xy in (pl[1:] if pl else []):
                if isinstance(xy, list) and xy and xy[0] == "xy":
                    xys.append((float(xy[1]), float(xy[2])))
            if len(xys) < 3:
                continue
            xs = [p[0] for p in xys]
            ys = [p[1] for p in xys]
            zb = (min(xs), min(ys), max(xs), max(ys))
            for zl in use_layers:
                if isinstance(zl, str) and zl.endswith(".Cu"):
                    zones.append(Zone(net, zl, zb, filled=filled, polygon=tuple(xys)))
    return Board(fps, vias, tracks, edge, segments, zones)


# ---------------------------------------------------------------- box geometry
def gap(a: Box, b: Box) -> float:
    """Distance between two boxes (0 when they touch or overlap)."""
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


box_gap = gap  # fanout / steps alias


def overlap(a: Box, b: Box, tol: float = 0.005) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > tol and min(a[3], b[3]) - max(a[1], b[1]) > tol


def grow(a: Box, d: float) -> Box:
    return a[0] - d, a[1] - d, a[2] + d, a[3] + d


def union_area(boxes: list[Box], clip: Box, res: float = 0.05) -> float:
    """Area of the union of boxes inside clip (raster at res mm)."""
    nx, ny = max(1, round((clip[2] - clip[0]) / res)), max(1, round((clip[3] - clip[1]) / res))
    bs = [b for b in boxes if overlap(b, clip, 0)]
    n = 0
    for j in range(ny):
        y = clip[1] + (j + 0.5) * res
        row = [(b[0], b[2]) for b in bs if b[1] <= y <= b[3]]
        if not row:
            continue
        for i in range(nx):
            x = clip[0] + (i + 0.5) * res
            if any(x0 <= x <= x1 for x0, x1 in row):
                n += 1
    return n * res * res


# ---------------------------------------------------------------- free-area scan
def moved_courtyards(fp: Footprint, x: float, y: float, rot: float | None = None) -> list[Box]:
    cys = fp.local_cys or ([fp.local_cy] if fp.local_cy else [])
    if not cys:
        return []
    r = fp.rot if rot is None else rot
    out: list[Box] = []
    for isl in cys:
        pts = [rotate(px, py, r) for px, py in isl]
        out.append((x + min(p[0] for p in pts), y + min(p[1] for p in pts),
                    x + max(p[0] for p in pts), y + max(p[1] for p in pts)))
    return out


def moved_courtyard(fp: Footprint, x: float, y: float, rot: float | None = None) -> Box | None:
    cys = moved_courtyards(fp, x, y, rot)
    if not cys:
        return None
    return min(b[0] for b in cys), min(b[1] for b in cys), max(b[2] for b in cys), max(b[3] for b in cys)


def move(board: Board, ref: str, x: float, y: float, rot: float | None = None) -> None:
    """Move (and turn) a footprint in the model: pads and courtyard follow."""
    fp = board.footprints[ref]
    r = fp.rot if rot is None else rot
    for p in fp.pads:
        lx, ly = rotate(p.x - fp.x, p.y - fp.y, -fp.rot)
        dx, dy = rotate(lx, ly, r)
        w, h = p.box[2] - p.box[0], p.box[3] - p.box[1]
        if round((r - fp.rot) / 90) % 2:
            w, h = h, w
        p.x, p.y = x + dx, y + dy
        p.box = (p.x - w / 2, p.y - h / 2, p.x + w / 2, p.y + h / 2)
    fp.courtyards = moved_courtyards(fp, x, y, r)
    fp.courtyard = moved_courtyard(fp, x, y, r)
    fp.x, fp.y, fp.rot = x, y, r


def stage_move(src_pcb: Path, dst_pcb: Path, ref: str, x: float, y: float, rot: float | None = None) -> Path:
    """Write a staged copy of src_pcb to dst_pcb with footprint `ref` moved to (x, y[, rot])."""
    tree = sexp.parse(Path(src_pcb).read_text(encoding="utf-8"))
    found = False
    for f in sexp.find(tree, "footprint"):
        fref = ""
        for prop in sexp.find(f, "property"):
            if len(prop) > 2 and prop[1] == "Reference":
                fref = prop[2]
        if not fref:
            fp_text = [t for t in sexp.find(f, "fp_text") if len(t) > 2 and t[1] == "reference"]
            fref = fp_text[0][2] if fp_text else ""
        if fref != ref:
            continue
        found = True
        at = sexp.first(f, "at")
        old_rot = float(at[3]) if at and len(at) > 3 and _num(at[3]) else 0.0
        new_rot = old_rot if rot is None else rot
        drot = new_rot - old_rot
        if at is not None:
            at[1:] = [f"{x:g}", f"{y:g}"] + ([f"{new_rot:g}"] if new_rot else [])
        if drot:
            for p in sexp.find(f, "pad"):
                pat = sexp.first(p, "at")
                if pat is not None and len(pat) >= 3:
                    pdeg = float(pat[3]) if len(pat) > 3 and _num(pat[3]) else 0.0
                    ndeg = (pdeg + drot) % 360.0
                    pat[3:] = [f"{ndeg:g}"] if ndeg else []
    if not found:
        raise KeyError(f"footprint {ref!r} not in {src_pcb}")
    dst_pcb = Path(dst_pcb)
    dst_pcb.parent.mkdir(parents=True, exist_ok=True)
    dst_pcb.write_text(sexp.dumps(tree) + "\n", encoding="utf-8")
    return dst_pcb


def moved_pads(fp: Footprint, x: float, y: float, rot: float | None = None) -> list[Pad]:
    """fp's pads as they would sit at (x, y, rot); fp itself is not changed."""
    r = fp.rot if rot is None else rot
    out = []
    for p in fp.pads:
        lx, ly = rotate(p.x - fp.x, p.y - fp.y, -fp.rot)
        dx, dy = rotate(lx, ly, r)
        w, h = p.box[2] - p.box[0], p.box[3] - p.box[1]
        if round((r - fp.rot) / 90) % 2:
            w, h = h, w
        px, py = x + dx, y + dy
        out.append(Pad(p.ref, p.num, px, py, (px - w / 2, py - h / 2, px + w / 2, py + h / 2), p.net, p.layers, p.hole,
                       drill=p.drill))
    return out


def _cu(layers) -> set[str]:
    out = set()
    for lay in layers:
        if lay.endswith(".Cu"):
            out |= {"F.Cu", "In1.Cu", "In2.Cu", "B.Cu"} if lay.startswith("*") else {lay}
    return out


def fits(board: Board, ref: str, x: float, y: float, rot: float | None = None, edge_margin: float = 0.0,
         ignore: tuple[str, ...] = (), ignore_courtyard: tuple[str, ...] = (),
         pad_gap: float = 0.1) -> tuple[bool, list[str]]:
    """Would footprint ref fit at (x, y, rot)? Blockers: courtyard overlaps on the same side (checked per courtyard
    island), board edge, and copper of other footprints' pads (other net, a shared copper layer) closer than pad_gap.

    ignore: refs that do not count at all (the parts you move next).
    ignore_courtyard: refs whose courtyard does not count but whose pads still do."""
    fp = board.footprints[ref]
    cys = moved_courtyards(fp, x, y, rot)
    if not cys:
        return False, [f"{ref} has no courtyard"]
    why = []
    mine = moved_pads(fp, x, y, rot)
    for other in board.footprints.values():
        if other.ref in (ref, *ignore):
            continue
        for q in other.pads:
            for p in mine:
                if p.net and p.net == q.net or not (_cu(p.layers) & _cu(q.layers)):
                    continue
                if overlap(grow(p.box, pad_gap), q.box, 0.0):
                    why.append(f"pad {p.name} [{p.net}] within {pad_gap} mm of pad {q.name} [{q.net}]")
    for other in board.footprints.values():
        if other.ref in (ref, *ignore, *ignore_courtyard) or other.side != fp.side:
            continue
        other_cys = other.courtyards or ([other.courtyard] if other.courtyard else [])
        if not other_cys:
            continue
        for cy in cys:
            hit = False
            for ocy in other_cys:
                if overlap(cy, ocy):
                    ox = min(cy[2], ocy[2]) - max(cy[0], ocy[0])
                    oy = min(cy[3], ocy[3]) - max(cy[1], ocy[1])
                    why.append(f"courtyard overlaps {other.ref} by {ox:.2f} x {oy:.2f} mm")
                    hit = True
                    break
            if hit:
                break
    e = board.edge
    if e:
        for cy in cys:
            if (cy[0] < e[0] + edge_margin or cy[1] < e[1] + edge_margin or cy[2] > e[2] - edge_margin
                    or cy[3] > e[3] - edge_margin):
                why.append(f"courtyard {_fmt(cy)} outside the board {_fmt(e)} (edge margin {edge_margin} mm)")
                break
    return not why, why


def scan(board: Board, ref: str, near: tuple[float, float], radius: float = 2.0, step: float = 0.1,
         rots: tuple[float, ...] | None = None, edge_margin: float = 0.0, limit: int = 5,
         ignore: tuple[str, ...] = (), ignore_courtyard: tuple[str, ...] = (),
         pad_gap: float = 0.1) -> list[tuple[float, float, float, float]]:
    """Free positions for ref within radius of near, nearest first: [(dist, x, y, rot)]."""
    fp = board.footprints[ref]
    rots = rots if rots is not None else (fp.rot,)
    out = []
    n = int(radius / step)
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            d = math.hypot(i * step, j * step)
            if d > radius + 1e-9:
                continue
            x, y = round(near[0] + i * step, 4), round(near[1] + j * step, 4)
            for r in rots:
                if fits(board, ref, x, y, r, edge_margin, ignore, ignore_courtyard, pad_gap)[0]:
                    out.append((round(d, 3), x, y, r))
    out.sort()
    return out[:limit]


def _fmt(b: Box) -> str:
    return "(" + ", ".join(f"{v:.2f}" for v in b) + ")"

load_board = load
