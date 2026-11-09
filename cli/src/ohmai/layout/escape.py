# SPDX-License-Identifier: Apache-2.0
"""Escape-room placement rules (lessons of a hand-routed QFN board; see docs/LAYOUT.md "Escape room").

Run on the placed, not yet routed board (rule 4 only applies then). Every rule returns findings
[(rule, status, text)] with status PASS / FAIL / INFO. Plain Python on the placement model; numbers come from the
project's layout config (`escape_room:` section), defaults from the board's Default net class.

  1 toe-gap        copper gap from each QFN side's pin toes to other parts' pads >= via + 2 x clearance
  2 flash-facing   the flash's pins that share nets with the MCU see the MCU without crossing the flash body
  3 edge-lane      no small part between a cap of the MCU's column and the edge (castellation) pads it faces
  4 ring-vias      no plane / stitching vias in the escape ring before routing
  5 inductor       each inductor pad faces the switch-node pins it connects to
  6 ring-fill      courtyards fill at most ring_fill_max (0.6) of the ring around the MCU courtyard
  7 side-capacity  per QFN side: signal pins that must escape <= outward lanes + via sites under the body
"""

from __future__ import annotations

import math
import re

from ohmai.layout.placement import Board, Box, Footprint, Pad, gap, grow, overlap, union_area

DEFAULTS = {
    "parts": [],            # [{ref, exempt: [refs], skip_nets: [regex]}]: the QFN/QFP packages to check
    "flash": [],            # [{ref, to}]
    "inductors": [],        # [{ref, to}]
    "edge_parts": [],       # refs of castellation / edge-connector footprints
    "column_reach": 1.5,    # mm: small parts this close to the MCU courtyard form its cap column
    "small_max_pads": 3,
    "ring_fill_width": 2.0,
    "ring_fill_max": 0.6,
    "lane_band": 1.0,       # mm beyond the toe gap that outward lanes are counted in
    "plane_nets": [],       # nets whose vias are plane / stitching vias (default: layout planes)
    "via_d": None, "via_h": None, "clearance": None, "track_w": None, "hole_to_hole": None,
}


def _rx(pats):
    return [re.compile(p) for p in pats or []]


def body_pads(fp: Footprint) -> tuple[list[Pad], Pad | None]:
    """Perimeter pins and the exposed pad (the largest pad over the centre), numbered copper pads only."""
    pads = [p for p in fp.pads if p.num and not p.hole]
    ep = None
    for p in pads:
        if p.box[0] <= fp.x <= p.box[2] and p.box[1] <= fp.y <= p.box[3]:
            if ep is None or _area(p.box) > _area(ep.box):
                ep = p
    if ep is None:
        return pads, None
    inside = lambda p: ep.box[0] <= p.x <= ep.box[2] and ep.box[1] <= p.y <= ep.box[3]  # noqa: E731
    return [p for p in pads if p is not ep and p.num != ep.num and not inside(p)], ep


def _area(b: Box) -> float:
    return (b[2] - b[0]) * (b[3] - b[1])


def sides(fp: Footprint) -> dict[str, list[Pad]]:
    """QFN pins by side (L/R/T/B) from their offset to the package centre."""
    pins, _ = body_pads(fp)
    out: dict[str, list[Pad]] = {"L": [], "R": [], "T": [], "B": []}
    for p in pins:
        dx, dy = p.x - fp.x, p.y - fp.y
        s = ("R" if dx > 0 else "L") if abs(dx) > abs(dy) else ("B" if dy > 0 else "T")
        out[s].append(p)
    return {k: v for k, v in out.items() if v}


def _toe_box(ps: list[Pad]) -> Box:
    return (min(p.box[0] for p in ps), min(p.box[1] for p in ps), max(p.box[2] for p in ps), max(p.box[3] for p in ps))


def _outward(s):
    return {"L": (-1, 0), "R": (1, 0), "T": (0, -1), "B": (0, 1)}[s]


class Rules:
    def __init__(self, board: Board, cfg: dict, rules: dict):
        self.b = board
        self.c = {**DEFAULTS, **(cfg or {})}
        self.via_d = self.c["via_d"] or rules.get("via_d", 0.45)
        self.via_h = self.c["via_h"] or rules.get("via_h", 0.2)
        self.cl = self.c["clearance"] or rules.get("clearance", 0.1)
        self.w = self.c["track_w"] or rules.get("track_w", 0.15)
        self.h2h = self.c["hole_to_hole"] or rules.get("hole_to_hole", 0.25)
        self.ring = self.via_d + 2 * self.cl
        self.plane_nets = set(self.c["plane_nets"] or rules.get("plane_nets", []))

    # ------------------------------------------------------------ helpers
    def _part(self, spec):
        return self.b.footprints.get(spec["ref"]) if isinstance(spec, dict) else self.b.footprints.get(spec)

    def _others_pads(self, fp: Footprint, exempt=()):
        return [q for o in self.b.footprints.values() if o.ref not in (fp.ref, *exempt, *self.c["edge_parts"])
                and o.side == fp.side for q in o.pads if q.layers]

    def side_gaps(self, fp: Footprint, exempt=()) -> dict[str, tuple[float, str]]:
        """per side: (min copper gap from the side's pins to other pads, 'other~pin')."""
        out = {}
        others = self._others_pads(fp, exempt)
        for s, ps in sides(fp).items():
            best = (math.inf, "")
            for p in ps:
                for q in others:
                    d = gap(p.box, q.box)
                    if d < best[0]:
                        best = (d, f"{q.name}~{p.name}")
            out[s] = best
        return out

    def _small(self, fp: Footprint) -> bool:
        return 0 < len({p.num for p in fp.pads if p.num}) <= self.c["small_max_pads"] and fp.courtyard is not None

    # ------------------------------------------------------------ rules
    def r1_toe_gap(self):
        f = []
        for spec in self.c["parts"]:
            fp = self._part(spec)
            if fp is None:
                f.append(("toe-gap", "FAIL", f"{spec['ref']}: not on the board"))
                continue
            for s, (d, who) in self.side_gaps(fp, spec.get("exempt", ())).items():
                ok = d >= self.ring - 1e-6
                f.append(("toe-gap", "PASS" if ok else "FAIL",
                          f"{fp.ref} side {s}: toe gap {d:.2f} mm ({who}) {'>=' if ok else '<'} "
                          f"{self.ring:.2f} (via {self.via_d} + 2 x {self.cl})"))
        return f

    def r2_flash(self):
        """The flash faces its pins when its links to the MCU can run side by side without crossing: going round the
        flash, its linked pins come in the reverse cyclic order of the MCU pins they go to (a planar fan)."""
        f = []
        for spec in self.c["flash"]:
            fl, mcu = self._part(spec), self.b.footprints.get(spec.get("to", ""))
            if fl is None or mcu is None:
                f.append(("flash-facing", "FAIL", f"{spec}: part not on the board"))
                continue
            pins, _ = body_pads(fl)
            mnets = {p.net: p for p in body_pads(mcu)[0] if p.net}
            links = [(p, mnets[p.net]) for p in pins if p.net and p.net in mnets and p.net not in self.plane_nets]
            ang = lambda p, c: math.atan2(p.y - c.y, p.x - c.x)  # noqa: E731
            a_ = [p.net for p, _ in sorted(links, key=lambda pq: ang(pq[0], fl))]
            b_ = [q.net for _, q in sorted(links, key=lambda pq: ang(pq[1], mcu))][::-1]
            order_ok = bool(links) and _cyclic_equal(a_, b_)
            fs = sides(fl)
            away = []
            if set(fs) in ({"L", "R"}, {"T", "B"}):
                # two pin columns: they face the MCU when it lies off the column ends, not off one column's side
                dx, dy = mcu.x - fl.x, mcu.y - fl.y
                along, across = (abs(dy), abs(dx)) if set(fs) == {"L", "R"} else (abs(dx), abs(dy))
                if along < across:
                    away.append(f"its pin columns run across the line to {mcu.ref}")
            else:
                side_of = {p.name: s for s, ps in fs.items() for p in ps}
                for p, q in links:
                    nx, ny = _outward(side_of[p.name])
                    d = math.dist((p.x, p.y), (q.x, q.y)) or 1.0
                    if (nx * (q.x - p.x) + ny * (q.y - p.y)) / d < -0.5:   # more than 120 degrees off its MCU pin
                        away.append(f"{p.name} (side {side_of[p.name]}) faces away")
            names = {p.net: f"{p.num}->{q.num}" for p, q in links}
            why = []
            if not order_ok:
                why.append(f"pin order does not match ({fl.ref} {' '.join(names[n] for n in a_)} vs {mcu.ref} "
                           f"{' '.join(names[n] for n in b_)}: links cross; mirror the flash or swap sides)")
            if away:
                why.append(f"{', '.join(away)} (turn the flash)")
            f.append(("flash-facing", "FAIL" if why or not links else "PASS",
                      f"{fl.ref}: {len(links)} link(s) to {mcu.ref}, " +
                      ("; ".join(why) if why else "pins face it, order matches")))
        return f

    def r3_edge_lane(self):
        f = []
        edge_pads = [q for r in self.c["edge_parts"] if r in self.b.footprints for q in self.b.footprints[r].pads]
        for spec in self.c["parts"]:
            mcu = self._part(spec)
            if mcu is None or mcu.courtyard is None or not edge_pads:
                continue
            small = [o for o in self.b.footprints.values() if o.ref != mcu.ref and o.side == mcu.side and self._small(o)
                     and o.ref not in self.c["edge_parts"]]
            column = [o for o in small if gap(o.courtyard, mcu.courtyard) <= self.c["column_reach"]]
            bad = set()
            for c in column:
                # the lane: from the column part outwards (away from the MCU) to the nearest edge pad
                dx, dy = c.x - mcu.x, c.y - mcu.y
                horiz = abs(dx) * (mcu.courtyard[3] - mcu.courtyard[1]) >= abs(dy) * (mcu.courtyard[2] - mcu.courtyard[0])
                cands = [q for q in edge_pads if (q.x - c.x) * dx > 0] if horiz else [q for q in edge_pads if (q.y - c.y) * dy > 0]
                cands = [q for q in cands if ((q.box[1] < c.courtyard[3] and q.box[3] > c.courtyard[1]) if horiz else
                                              (q.box[0] < c.courtyard[2] and q.box[2] > c.courtyard[0]))]
                if not cands:
                    continue
                e = min(cands, key=lambda q: gap(q.box, c.courtyard))
                if horiz:
                    x0, x1 = (c.courtyard[2], e.box[0]) if dx > 0 else (e.box[2], c.courtyard[0])
                    lane = (x0, c.courtyard[1], x1, c.courtyard[3])
                else:
                    y0, y1 = (c.courtyard[3], e.box[1]) if dy > 0 else (e.box[3], c.courtyard[1])
                    lane = (c.courtyard[0], y0, c.courtyard[2], y1)
                if lane[2] - lane[0] <= 0 or lane[3] - lane[1] <= 0:
                    continue
                for s in small:
                    if s is not c and s not in column and overlap(s.courtyard, lane, 0.05):
                        bad.add(f"{s.ref} between {c.ref} and {e.name}")
            f.append(("edge-lane", "FAIL" if bad else "PASS",
                      f"{mcu.ref}: " + ("; ".join(sorted(bad)) if bad else
                                        f"no small part between its {len(column)} column part(s) and the edge pads")))
        return f

    def ring_box(self, fp: Footprint) -> tuple[Box, Box]:
        pins, _ = body_pads(fp)
        toe = _toe_box(pins)
        return toe, grow(toe, self.ring)

    def r4_ring_vias(self):
        f = []
        routed = [t for t in self.b.tracks if t[0] and t[0] not in self.plane_nets]
        for spec in self.c["parts"]:
            fp = self._part(spec)
            if fp is None:
                continue
            if routed:
                f.append(("ring-vias", "INFO", f"{fp.ref}: board has {len(routed)} signal track(s); the ring-via "
                                               "rule applies before routing only"))
                continue
            toe, ring = self.ring_box(fp)
            hits = [v for v in self.b.vias if v.net in self.plane_nets and ring[0] <= v.x <= ring[2]
                    and ring[1] <= v.y <= ring[3] and not (toe[0] < v.x < toe[2] and toe[1] < v.y < toe[3])]
            f.append(("ring-vias", "FAIL" if hits else "PASS",
                      f"{fp.ref}: " + (", ".join(f"{v.net} via at {v.x:.2f},{v.y:.2f}" for v in hits) +
                                       " in the escape ring" if hits else "no plane/stitch via in the escape ring")))
        return f

    def r5_inductor(self):
        f = []
        for spec in self.c["inductors"]:
            ind, ic = self._part(spec), self.b.footprints.get(spec.get("to", ""))
            if ind is None or ic is None:
                f.append(("inductor", "FAIL", f"{spec}: part not on the board"))
                continue
            lp = [p for p in ind.pads if p.num]
            bad, n = [], 0
            for p in lp:
                ipins = [q for q in ic.pads if q.net and q.net == p.net]
                if spec.get("nets") and not any(re.search(r, p.net) for r in spec["nets"]):
                    continue
                if not ipins:
                    continue
                n += 1
                tx, ty = sum(q.x for q in ipins) / len(ipins), sum(q.y for q in ipins) / len(ipins)
                mine = math.dist((p.x, p.y), (tx, ty))
                other = min((math.dist((o.x, o.y), (tx, ty)) for o in lp if o.net != p.net), default=math.inf)
                if mine > other + 1e-6:
                    bad.append(f"{p.name} [{p.net}] is the far pad from {ic.ref} pins {','.join(q.num for q in ipins)}")
            st = "FAIL" if bad or not n else "PASS"
            f.append(("inductor", st, f"{ind.ref}: " + ("; ".join(bad) if bad else
                                                       f"{n} switch-node pad(s) face {ic.ref}" if n else
                                                       f"shares no net with {ic.ref}")))
        return f

    def r6_ring_fill(self):
        f = []
        for spec in self.c["parts"]:
            fp = self._part(spec)
            if fp is None or fp.courtyard is None:
                continue
            outer = grow(fp.courtyard, self.c["ring_fill_width"])
            boxes = [cy for o in self.b.footprints.values() if o.ref != fp.ref and o.side == fp.side
                     and o.ref not in self.c["edge_parts"]
                     for cy in (o.courtyards or ([o.courtyard] if o.courtyard else []))]
            used = union_area(boxes, outer) - union_area(boxes, fp.courtyard)
            ring_a = _area(outer) - _area(fp.courtyard)
            frac = used / ring_a if ring_a else 0
            ok = frac <= self.c["ring_fill_max"] + 1e-9
            f.append(("ring-fill", "PASS" if ok else "FAIL",
                      f"{fp.ref}: courtyards fill {frac:.0%} of the {self.c['ring_fill_width']} mm ring "
                      f"({'<=' if ok else '>'} {self.c['ring_fill_max']:.0%})"))
        return f

    def side_capacity(self, fp: Footprint, spec: dict) -> dict[str, dict]:
        skip = _rx(spec.get("skip_nets"))
        nets_elsewhere = {q.net for o in self.b.footprints.values() if o.ref != fp.ref for q in o.pads if q.net}
        pins, ep = body_pads(fp)
        gaps = self.side_gaps(fp, spec.get("exempt", ()))
        others = self._others_pads(fp, spec.get("exempt", ()))
        out = {}
        for s, ps in sides(fp).items():
            demand = sorted({p.net for p in ps if p.net and p.net not in self.plane_nets and p.net in nets_elsewhere
                             and not p.net.startswith("unconnected-") and not any(r.search(p.net) for r in skip)})
            toe = _toe_box(ps)
            ox, oy = _outward(s)
            horiz = ox != 0
            lo, hi = (toe[1], toe[3]) if horiz else (toe[0], toe[2])
            lo, hi = lo - self.ring, hi + self.ring     # tracks may fan out past the corner pins
            # outward lanes: free length along the side in the band beyond the toes
            g0 = toe[2] if ox > 0 else toe[0] if ox < 0 else toe[3] if oy > 0 else toe[1]
            g1 = g0 + (ox or oy) * (self.ring + self.c["lane_band"])
            band = ((min(g0, g1), lo, max(g0, g1), hi) if horiz else (lo, min(g0, g1), hi, max(g0, g1)))
            blocked = []
            for q in others:
                bq = grow(q.box, self.cl + self.w / 2)
                if overlap(bq, band, 0):
                    blocked.append((bq[1], bq[3]) if horiz else (bq[0], bq[2]))
            free = _free_len(lo, hi, blocked)
            pitch = self.w + self.cl
            lanes = sum(int((L + self.cl) / pitch) for L in free) if gaps[s][0] >= self.w + 2 * self.cl else 0
            # via sites under the body, between the pin heels and the exposed pad
            heel = (min(p.box[0] for p in ps) if ox > 0 else max(p.box[2] for p in ps) if ox < 0 else
                    min(p.box[1] for p in ps) if oy > 0 else max(p.box[3] for p in ps))
            if ep is not None:
                inner = ep.box[2] if ox > 0 else ep.box[0] if ox < 0 else ep.box[3] if oy > 0 else ep.box[1]
                depth = abs(heel - inner) - self.cl
            else:
                depth = abs(heel - (fp.x if horiz else fp.y))
            rows = max(0, int(depth / (self.via_d + self.cl)))
            vpitch = max(self.via_d + self.cl, self.via_h + self.h2h)
            sites = rows * int((hi - lo - 2 * self.ring) / vpitch)
            out[s] = {"demand": demand, "lanes": lanes, "via_sites": sites, "capacity": lanes + sites}
        return out

    def r7_capacity(self):
        f = []
        for spec in self.c["parts"]:
            fp = self._part(spec)
            if fp is None:
                continue
            for s, c in self.side_capacity(fp, spec).items():
                ok = len(c["demand"]) <= c["capacity"]
                f.append(("side-capacity", "PASS" if ok else "FAIL",
                          f"{fp.ref} side {s}: {len(c['demand'])} signal pin(s) to escape, capacity {c['capacity']} "
                          f"({c['lanes']} outward lane(s) + {c['via_sites']} via site(s) under the body)"))
        return f

    def run(self):
        return (self.r1_toe_gap() + self.r2_flash() + self.r3_edge_lane() + self.r4_ring_vias() + self.r5_inductor()
                + self.r6_ring_fill() + self.r7_capacity())


def _free_len(lo, hi, blocked):
    segs, cur = [], lo
    for a, b in sorted(blocked):
        if a > cur:
            segs.append(min(a, hi) - cur)
        cur = max(cur, b)
        if cur >= hi:
            break
    if cur < hi:
        segs.append(hi - cur)
    return [s for s in segs if s > 0]


def _cyclic_equal(a: list, b: list) -> bool:
    if len(a) != len(b):
        return False
    if not a:
        return True
    k = [i for i, v in enumerate(b) if v == a[0]]
    return any(b[i:] + b[:i] == a for i in k)


def _segs_cross(a, b, c, d) -> bool:
    """Proper crossing of segments a-b and c-d (shared end points and touching do not count)."""
    def orient(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    return (orient(a, b, c) * orient(a, b, d) < 0) and (orient(c, d, a) * orient(c, d, b) < 0)


def _seg_hits_box(a, b, box: Box) -> bool:
    """Does segment a-b pass through the open interior of box (Liang-Barsky)?"""
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return False
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if abs(p) < 1e-12:
            if q <= 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 >= t1:
            return False
    return t1 - t0 > 1e-9
