# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Pour integrity on a layer shared by a pour and signals (shapely; no KiCad): piece count, neck width, and neck (x, y).

The pour is the real zone fill or a model (zone outline minus other-net copper grown by the zone clearance), so a
route can be tested before it is committed. A neck of width w survives a morphological opening by w (erode w/2,
dilate w/2); the narrowest neck between attach points (the pour net's vias and PTH pads) is the largest w for
which all of them still lie in one piece of the opened pour. Limits come from the layout config `pour:` section.
"""

from __future__ import annotations

from pathlib import Path

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import nearest_points, unary_union

from .. import sexp
from .placement import load as load_board


def pieces(g):
    if g.is_empty:
        return []
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]


def model_pour(outline, other, holes_other, edges, zone_cl: float, edge_cl: float):
    """outline: zone outline; other: [(geom, clearance)] other-net copper on the layer; holes_other: hole polygons
    of other-net items; edges: board edge geometry."""
    cut = [g.buffer(cl, 8) for g, cl in other] + [h.buffer(zone_cl, 8) for h in holes_other]
    cut.append(edges.buffer(edge_cl, 8))
    return outline.difference(unary_union(cut))


def connected(pour, pts, w: float, attach: float):
    g = pour.buffer(-w / 2, 8).buffer(w / 2, 8) if w > 0 else pour
    ps = pieces(g)
    hit = set()
    for x, y in pts:
        p = Point(x, y)
        k = next((i for i, q in enumerate(ps) if q.distance(p) < attach), None)
        if k is None:
            return False, ps
        hit.add(k)
    return len(hit) == 1, ps


def neck_with_xy(
    pour, pts, attach: float, lo: float = 0.0, hi: float = 2.0, tol: float = 0.02
) -> tuple[float, tuple[float, float] | None]:
    if not pts:
        return 0.0, None
    ok0, _ = connected(pour, pts, lo, attach)
    if not ok0:
        return 0.0, (round(pts[0][0], 2), round(pts[0][1], 2))
    # A wide current corridor can exceed the default bracket; never cap the neck at hi.
    x0, y0, x1, y1 = pour.bounds
    limit = max(x1 - x0, y1 - y0)
    while hi < limit and connected(pour, pts, hi, attach)[0]:
        lo, hi = hi, min(hi * 2, limit)
    while hi - lo > tol:
        m = (lo + hi) / 2
        if connected(pour, pts, m, attach)[0]:
            lo = m
        else:
            hi = m
    g_hi = pour.buffer(-hi / 2, 8).buffer(hi / 2, 8)
    ps_hi = pieces(g_hi)
    groups: dict[int, list[tuple[float, float]]] = {}
    detached: list[tuple[float, float]] = []
    for x, y in pts:
        p = Point(x, y)
        k = next((i for i, q in enumerate(ps_hi) if q.distance(p) < attach), None)
        if k is None:
            detached.append((x, y))
        else:
            groups.setdefault(k, []).append((x, y))
    xy: tuple[float, float] | None = None
    if len(groups) >= 2:
        keys = list(groups.keys())
        best_d = 1e9
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                pa, pb = nearest_points(ps_hi[keys[i]], ps_hi[keys[j]])
                d = pa.distance(pb)
                if d < best_d:
                    best_d = d
                    xy = (round((pa.x + pb.x) / 2, 2), round((pa.y + pb.y) / 2, 2))
    elif detached:
        xy = (round(detached[0][0], 2), round(detached[0][1], 2))
    return lo, xy


def neck(pour, pts, attach: float, lo: float = 0.0, hi: float = 2.0, tol: float = 0.02) -> float:
    w, _ = neck_with_xy(pour, pts, attach, lo=lo, hi=hi, tol=tol)
    return w


def piece_count(pour, pts, attach: float):
    """pieces of the pour that hold at least one attach point (what the fill keeps), and the total area."""
    ps = pieces(pour)
    return sum(1 for q in ps if any(q.distance(Point(x, y)) < attach for x, y in pts)), sum(q.area for q in ps)


def report(pour, pts, local, cfg: dict) -> dict:
    n, area = piece_count(pour, pts, cfg["attach"])
    nk, nk_xy = neck_with_xy(pour, pts, cfg["attach"]) if pts else (0.0, None)
    nl, nl_xy = neck_with_xy(pour, local, cfg["attach"]) if len(local) > 1 else (None, None)
    return {
        "pieces": n,
        "area": round(area, 2),
        "neck": round(nk, 2),
        "neck_xy": nk_xy,
        "neck_local": nl and round(nl, 2),
        "neck_local_xy": nl_xy if nl is not None else None,
    }


def verdict(r: dict, cfg: dict, base: dict | None = None) -> bool:
    """One piece, neck >= min_neck, local neck >= local_neck. With base (the board before a change), a board that
    was already below a limit may not get worse than it was."""
    mn, ml = cfg["min_neck"], cfg["local_neck"]
    if base:
        mn, ml = min(mn, base["neck"]), min(ml, base["neck_local"] or 9)
    return r["pieces"] == 1 and r["neck"] >= mn and (r["neck_local"] is None or r["neck_local"] >= ml)


def extract_filled_pour(pcb_path: Path, net: str, layer: str) -> tuple[object, list[tuple[float, float]], list[tuple[float, float]]]:
    """Read filled_polygon for (net, layer) and its attach points directly from .kicad_pcb S-expressions."""
    tree = sexp.parse(pcb_path.read_text(encoding="utf-8", errors="replace"))
    polys = []
    for z in sexp.find(tree, "zone"):
        nn = sexp.first(z, "net_name")
        if nn and len(nn) > 1 and nn[1] == net:
            for fp in sexp.find(z, "filled_polygon"):
                lay = sexp.first(fp, "layer")
                if lay and len(lay) > 1 and lay[1] == layer:
                    pts_node = sexp.first(fp, "pts")
                    if pts_node:
                        coords = [(float(xy[1]), float(xy[2])) for xy in sexp.find(pts_node, "xy")]
                        if len(coords) >= 3:
                            polys.append(Polygon(coords).buffer(0))
    geom = unary_union(polys) if polys else Polygon()
    b = load_board(pcb_path)
    pts = [(v.x, v.y) for v in b.vias if v.net == net] + [
        (p.x, p.y) for p in b.pads() if p.net == net and (p.hole or layer in p.layers or "*.Cu" in p.layers)
    ]
    return geom, pts, []


def attach_features(pcb_path: Path, net: str, layer: str) -> list[dict]:
    """Same-net copper that can join the pour on ``layer``: vias (all layers) and pads on it (incl. PTH)."""
    from .steps import _handroute_pad_shape

    b = load_board(pcb_path)
    out = [dict(kind="via", name=f"via@{v.x:.3f},{v.y:.3f}", xy=(v.x, v.y), shape=Point(v.x, v.y).buffer(v.d / 2, 16))
           for v in b.vias if v.net == net]
    for p in b.pads():
        if p.net == net and (p.hole or layer in p.layers or "*.Cu" in p.layers):
            shape = _handroute_pad_shape(p)
            if shape.is_empty or shape.area < 1e-6:
                shape = Point(p.x, p.y).buffer(max(p.drill / 2, 0.05), 16)
            out.append(dict(kind="pad", name=p.name, xy=(p.x, p.y), shape=shape))
    return out


def patch_attach(pour, features: list[dict], tol: float = 0.01, ring: float = 1.0):
    """Measure pours as copper, not as fill artifacts around their own attach points.

    Attach features that do not touch the pour (a via used only by tracks on other layers) are
    returned separately instead of failing the neck. Touching features are merged as copper, and a
    pour hole around one of them (it holds the via/pad centre or touches its copper, and lies within
    ``ring`` mm of it: a drill hole or a thermal-relief gap sector) is filled. Other-net holes, which really narrow
    the pour, are kept.
    Returns (patched pour, attached features, unattached features, holes filled)."""
    attached = [f for f in features if pour.distance(f["shape"]) <= tol]
    unattached = [f for f in features if f not in attached]
    filled = 0
    patched = []
    for q in pieces(pour):
        keep = []
        for interior in q.interiors:
            hole = Polygon(interior)
            if any((hole.covers(Point(f["xy"])) or hole.distance(f["shape"]) <= tol)
                   and hole.within(f["shape"].buffer(ring, 8)) for f in attached):
                filled += 1
            else:
                keep.append(interior)
        patched.append(Polygon(q.exterior, keep))
    geom = unary_union(patched + [f["shape"] for f in attached]) if (patched or attached) else pour
    return geom, attached, unattached, filled


def measure(pour, features: list[dict], local_box, cfg: dict) -> dict:
    """``report`` on the attach-patched pour, plus the raw (unpatched) neck for transparency."""
    raw_pts = [f["xy"] for f in features]
    raw_neck, raw_xy = neck_with_xy(pour, raw_pts, cfg["attach"]) if raw_pts else (0.0, None)
    geom, attached, unattached, filled = patch_attach(pour, features)
    pts = [f["xy"] for f in attached]
    local = [(x, y) for x, y in pts if local_box and local_box[0] <= x <= local_box[2] and local_box[1] <= y <= local_box[3]]
    rep = report(geom, pts, local, cfg)
    detail = []
    for q in sorted(pieces(geom), key=lambda q: -q.area):
        own = [xy for xy in pts if q.distance(Point(xy)) < cfg["attach"]]
        if not own:
            continue
        nk, nk_xy = neck_with_xy(q, own, cfg["attach"]) if len(own) > 1 else (None, None)
        detail.append(dict(area=round(q.area, 2), attach_points=len(own),
                           neck=None if nk is None else round(nk, 2), neck_xy=nk_xy))
    rep["pieces_detail"] = detail
    rep.update(neck_raw=round(raw_neck, 2), neck_raw_xy=raw_xy, attach_points=len(pts), holes_patched=filled,
               unattached=[dict(kind=f["kind"], name=f["name"], xy=[round(f["xy"][0], 3), round(f["xy"][1], 3)])
                           for f in unattached])
    return rep


def width_at(pour, x: float, y: float, directions: int = 180) -> float | None:
    """Minimum chord through (x, y) over all directions: the local perpendicular width.

    Unlike a cut at x = constant this does not depend on the cut direction, and a cut that runs
    along an outline edge cannot read as copper width. None when (x, y) is not inside the pour."""
    import math

    from shapely.geometry import LineString

    p = Point(x, y)
    piece = next((q for q in pieces(pour) if q.covers(p)), None)
    if piece is None:
        return None
    x0, y0, x1, y1 = piece.bounds
    reach = math.hypot(x1 - x0, y1 - y0) + 1.0
    best = None
    for k in range(directions):
        t = math.pi * k / directions
        dx, dy = math.cos(t) * reach, math.sin(t) * reach
        cut = piece.intersection(LineString([(x - dx, y - dy), (x + dx, y + dy)]))
        segs = [g for g in getattr(cut, "geoms", [cut]) if g.geom_type == "LineString"]
        own = [g for g in segs if g.distance(p) < 1e-9]
        if own:
            length = min(g.length for g in own)
            best = length if best is None else min(best, length)
    return best


def section(pour, axis: str, value: float, step: float = 0.05) -> list[dict]:
    """Copper crossed by the line ``axis`` = ``value`` (x or y), one row per crossing.

    ``chord`` is the raw length along the cut; ``width`` is the diameter of the largest disk that
    fits in the copper on that crossing (2 x max distance to the outline), which equals the
    perpendicular width of a straight run however the cut is angled. Crossings that only run along
    an outline edge (zero width) are dropped."""
    from shapely.geometry import LineString

    if pour.is_empty:
        return []
    x0, y0, x1, y1 = pour.bounds
    line = (LineString([(value, y0 - 1), (value, y1 + 1)]) if axis == "x"
            else LineString([(x0 - 1, value), (x1 + 1, value)]))
    cut = pour.intersection(line)
    rows = []
    boundary = pour.boundary
    for g in getattr(cut, "geoms", [cut]):
        if g.geom_type != "LineString" or g.length <= 1e-6:
            continue
        n = max(2, min(400, int(g.length / step) + 1))
        best, at, at_i = 0.0, None, 0
        for i in range(n + 1):
            q = g.interpolate(i / n, normalized=True)
            d = boundary.distance(q)
            if d > best:
                best, at, at_i = d, q, i
        lo_t, hi_t = max(0.0, (at_i - 1) / n), min(1.0, (at_i + 1) / n)
        for _ in range(40):  # refine the sampled maximum (distance is unimodal near it)
            m1, m2 = lo_t + (hi_t - lo_t) / 3, hi_t - (hi_t - lo_t) / 3
            if boundary.distance(g.interpolate(m1, normalized=True)) < boundary.distance(g.interpolate(m2, normalized=True)):
                lo_t = m1
            else:
                hi_t = m2
        q = g.interpolate((lo_t + hi_t) / 2, normalized=True)
        if boundary.distance(q) > best:
            best, at = boundary.distance(q), q
        if best <= 1e-4:
            continue  # the cut runs along an edge: no copper width here
        a, b = g.coords[0], g.coords[-1]
        rows.append(dict(axis=axis, value=value, start=[round(a[0], 3), round(a[1], 3)],
                         end=[round(b[0], 3), round(b[1], 3)], chord=round(g.length, 3),
                         width=round(2 * best, 3), widest_at=[round(at.x, 3), round(at.y, 3)]))
    return rows
