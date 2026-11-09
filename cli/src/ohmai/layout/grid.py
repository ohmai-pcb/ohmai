"""Grid router core for `ohmai layout handroute` (numpy; no KiCad): A* over copper layers on a raster, path
simplification, the via spacing rules, and the hand-route record bookkeeping.

Two fixes carried over from the first board that used this router:
- via spacing: KiCad's hole_to_hole rule holds between ANY two holes, own net included. The search must not
  change layer again within hole-to-hole reach of a via it just placed (`near_path_via`), and a finished route is
  checked pairwise (`vias_too_close`).
- rip records: ripping a net's copper (`handroute route --rip RE`) must also drop that net's earlier hand-route
  records (manual entries stay), or the next replay lays the new route on top of the old one (`drop_ripped`).
- terminals: a connection end is the whole same-net copper island it belongs to, not the one item the DRC
  report names (`copper_island`). A hand-drawn track ending on a via otherwise offers only its own layer's
  cells as start/goal; in a boxed-in pad area that is an instant "no path" with no rip-up candidates.
"""

from __future__ import annotations

import heapq
import math
import re
import time
from dataclasses import dataclass

import numpy as np

DIRS = [(0, 1, 1.0), (1, 0, 1.0), (0, -1, 1.0), (-1, 0, 1.0),
        (1, 1, 1.4142), (1, -1, 1.4142), (-1, 1, 1.4142), (-1, -1, 1.4142)]


@dataclass(frozen=True)
class Grid:
    x0: float
    y0: float
    nx: int
    ny: int
    res: float

    @classmethod
    def around(cls, box, res: float) -> Grid:
        """Grid covering box (x0, y0, x1, y1) in mm, origin snapped down to the resolution."""
        x0 = math.floor(box[0] / res + 1e-9) * res
        y0 = math.floor(box[1] / res + 1e-9) * res
        return cls(round(x0, 6), round(y0, 6), int(math.ceil((box[2] - x0) / res - 1e-9)),
                   int(math.ceil((box[3] - y0) / res - 1e-9)), res)

    def xy(self, ix: int, iy: int) -> tuple[float, float]:
        return self.x0 + (ix + 0.5) * self.res, self.y0 + (iy + 0.5) * self.res

    def cell(self, x: float, y: float) -> tuple[int, int]:
        return int((x - self.x0) / self.res), int((y - self.y0) / self.res)


def via_pitch_cells(via_h: float, hole_hole: float, res: float) -> float:
    """Closest same-net via centres in cells: hole diameter + hole-to-hole (the rule counts own-net holes too)."""
    return (via_h + hole_hole) / res + 1e-9


def near_path_via(parent: dict, s, y: int, x: int, pitch_cells: float) -> bool:
    """True if the partial path ending in s already changed layer within pitch_cells of (y, x)."""
    cur, steps = s, 0
    while cur is not None and steps < int(pitch_cells) + 4:
        prev = parent.get(cur)
        if prev is None:
            return False
        if prev[0] != cur[0] and math.hypot(cur[1] - y, cur[2] - x) < pitch_cells:
            return True
        if prev[1:] != cur[1:]:
            steps += 1
        cur = prev
    return False


class SearchBudget:
    """Shared wall-clock, expansion and search caps across all nets and widths."""

    def __init__(self, seconds=60.0, max_expand=2_500_000, max_searches=128):
        if not math.isfinite(seconds) or seconds <= 0 or max_expand < 1 or max_searches < 1:
            raise ValueError("routing budgets must be finite and positive")
        self.deadline = time.monotonic() + seconds
        self.max_expand = max_expand
        self.max_searches = max_searches
        self.expanded = 0
        self.searches = 0

    @property
    def reason(self):
        if time.monotonic() >= self.deadline:
            return "time_budget"
        if self.expanded >= self.max_expand:
            return "expansion_budget"
        if self.searches >= self.max_searches:
            return "search_budget"
        return None


def astar(free: dict, viaok, starts, goals, layers, cost_mult: dict, res: float, via_cost: float,
          pitch_cells: float, pen: dict | None = None, vpen=None, max_expand: int = 2_500_000,
          budget: SearchBudget | None = None):
    """free[l]: NY x NX bool (track centre allowed), viaok: NY x NX bool, starts/goals: sets of (l, iy, ix).
    Returns the cell path [(l, iy, ix)] or None. Diagonal steps cost sqrt(2); a layer change costs via_cost (mm) and
    is refused within pitch_cells of the path's previous via."""
    if budget is not None:
        if budget.reason:
            return None
        budget.searches += 1
    if not goals or not starts:
        return None
    ny, nx = viaok.shape
    goal_l: dict = {}
    for l_, y, x in goals:
        goal_l.setdefault(l_, set()).add((y, x))
    gy = np.array([g[1] for g in goals])
    gx = np.array([g[2] for g in goals])
    cy, cx = gy.mean(), gx.mean()
    spread = (max(np.ptp(gy), np.ptp(gx)) * res) if len(goals) > 1 else 0.0

    def h(y, x):
        dy, dx = abs(y - cy), abs(x - cx)
        return (max(dx, dy) + 0.414 * min(dx, dy)) * res * 0.9 - spread

    best: dict = {}
    pq: list = []
    for s in starts:
        best[s] = 0.0
        heapq.heappush(pq, (h(s[1], s[2]), 0.0, s, None))
    parent: dict = {}
    n = 0
    while pq:
        # Search count is checked at entry; this in-flight search may finish at the cap.
        if budget is not None and (budget.expanded >= budget.max_expand or
                                   time.monotonic() >= budget.deadline):
            return None
        _f, g, s, par = heapq.heappop(pq)
        if g > best.get(s, 1e18) + 1e-9 or s in parent:
            continue
        parent[s] = par
        l_, y, x = s
        if (y, x) in goal_l.get(l_, ()):
            path = [s]
            while parent[path[-1]] is not None:
                path.append(parent[path[-1]])
            return path[::-1]
        n += 1
        if budget is not None:
            budget.expanded += 1
        if n > max_expand:
            return None
        for dy, dx, c in DIRS:
            yy, xx = y + dy, x + dx
            if 0 <= yy < ny and 0 <= xx < nx and free[l_][yy, xx]:
                ng = g + c * res * cost_mult[l_] + (pen[l_][yy, xx] if pen is not None else 0.0)
                t = (l_, yy, xx)
                if ng < best.get(t, 1e18):
                    best[t] = ng
                    heapq.heappush(pq, (ng + h(yy, xx), ng, t, s))
        if viaok[y, x] and not near_path_via(parent, s, y, x, pitch_cells):
            for l2 in layers:
                if l2 != l_ and free[l2][y, x]:
                    ng = g + via_cost + (vpen[y, x] if vpen is not None else 0.0)
                    t = (l2, y, x)
                    if ng < best.get(t, 1e18):
                        best[t] = ng
                        heapq.heappush(pq, (ng + h(y, x), ng, t, s))
    return None


def simplify(path, xy) -> list:
    """Cell path -> [(layer, [(x, y), ...])] runs with collinear points merged; xy(ix, iy) gives mm."""
    runs, cur = [], [path[0]]
    for p in path[1:]:
        if p[0] != cur[-1][0]:
            runs.append(cur)
            cur = [p]
        else:
            cur.append(p)
    runs.append(cur)
    out = []
    for r in runs:
        pts = [r[0]]
        for i in range(1, len(r) - 1):
            a, b, c = pts[-1], r[i], r[i + 1]
            d1 = (np.sign(b[1] - a[1]), np.sign(b[2] - a[2]))
            d2 = (np.sign(c[1] - b[1]), np.sign(c[2] - b[2]))
            if d1 != d2:
                pts.append(b)
        if len(r) > 1:
            pts.append(r[-1])
        out.append((r[0][0], [xy(p[2], p[1]) for p in pts]))
    return out


def runs_to_segments(runs) -> tuple[list, list]:
    """[(layer, pts)] -> segments [(layer, a, b)] and vias at the layer changes."""
    segs, vias = [], []
    for i, (l_, pts) in enumerate(runs):
        segs += [(l_, a, b) for a, b in zip(pts, pts[1:], strict=False)]
        if i + 1 < len(runs):
            vias.append(pts[-1])
    return segs, vias


def vias_too_close(vias, min_centre: float):
    """First pair of via centres closer than min_centre (hole + hole_to_hole), or None. Same net counts too."""
    for i, p in enumerate(vias):
        for q in vias[i + 1:]:
            if math.dist(p, q) < min_centre:
                return p, q
    return None


def drop_ripped(routes: list[dict], pattern: str) -> tuple[list[dict], int]:
    """Hand-route records of the nets matching pattern go when their copper is ripped; `manual ...` entries stay."""
    rx = re.compile(pattern)
    kept = [r for r in routes if not rx.search(r["net"]) or str(r.get("why", "")).startswith("manual")]
    return kept, len(routes) - len(kept)


def rip_names(names, pattern: str) -> list[str]:
    rx = re.compile(pattern)
    return sorted(n for n in names if rx.search(n))


def netclass(patterns, net: str) -> str:
    """Net class by netclass_patterns [(regex, class)], first match (as KiCad assigns it)."""
    for rx, c in patterns:
        if re.search(rx, net):
            return c
    return "Default"


def record(net: str, w: float, segs, vias, layer_name, why: str | None = None) -> dict:
    rec = {"net": net, "w": w, "tracks": [{"layer": layer_name(l_), "a": [round(a[0], 3), round(a[1], 3)],
                                           "b": [round(b[0], 3), round(b[1], 3)], "w": w} for l_, a, b in segs],
           "vias": [[round(x, 3), round(y, 3)] for x, y in vias]}
    if why is not None:
        rec["why"] = why
    return rec


def manual_records(entries: dict, names) -> list[dict]:
    """Records for manual geometry entries {name: [{net, w, runs: [[layer, [[x, y], ...]], ...]}]}."""
    out = []
    for name in names:
        if name not in entries:
            raise KeyError(f"no manual entry '{name}' (have: {', '.join(sorted(entries)) or 'none'})")
        for e in entries[name]:
            runs = [(r[0], [tuple(p) for p in r[1]]) for r in e["runs"]]
            segs, vias = runs_to_segments(runs)
            out.append({"net": e["net"], "w": e["w"], "why": f"manual {name}",
                        "tracks": [{"layer": lay, "a": list(a), "b": list(b), "w": e["w"]} for lay, a, b in segs],
                        "vias": [list(v) for v in vias]})
    return out


def copper_island(items: list, start: int, gap: float = 0.001, alive=None) -> list[int]:
    """Indices of the items in start's copper island: same net, touching on a shared layer, transitively.

    items: sequence of (net, {layer: shapely geometry}). A via is one item with a geometry on every layer it
    spans, so a track ending on it pulls in its other layers as terminal copper.

    alive: indices still present in the router model (None: all). Copper ripped up during a run must not
    count: a re-route that 'reaches' ripped copper ends at once and the net is saved unrouted. A ripped
    start gives an empty island."""
    if alive is not None and start not in alive:
        return []
    net0 = items[start][0]
    seen, todo = {start}, [start]
    while todo:
        a = todo.pop()
        ga = items[a][1]
        for j, (net, gj) in enumerate(items):
            if j in seen or net != net0 or (alive is not None and j not in alive):
                continue
            if any(lay in gj and gj[lay].distance(geo) <= gap for lay, geo in ga.items()):
                seen.add(j)
                todo.append(j)
    return sorted(seen)


def keep_records(records: list, ripped) -> list:
    """Route records that survive ripping the nets in ripped: other nets' records and manual ones
    (why starting with "manual"; hand-drawn copper is never ripped). Use the same rule for the router model,
    every checkpoint and the final save, or a rebuild brings ripped copper back / loses hand copper."""
    return [r for r in records if r["net"] not in ripped or str(r.get("why", "")).startswith("manual")]
