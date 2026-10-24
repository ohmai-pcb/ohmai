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
