# SPDX-License-Identifier: Apache-2.0
"""Sub-second placement, critical-loop, keepout, checklist, and pour-continuity checks on .kicad_pcb."""

from __future__ import annotations

import math
from pathlib import Path

import yaml

from ohmai.checks import FAIL, PASS, SKIP, Result, register
from ohmai.layout import config as lcfg
from ohmai.layout import placement as L
from ohmai.project import Project


def _resolve_pads(board: L.Board, spec: str, self_ref: str | None = None) -> tuple[L.Footprint | None, list[L.Pad]]:
    s = spec.strip()
    if self_ref and (s == "SELF" or s.startswith("SELF.") or s.startswith("SELF:")):
        s = self_ref + s[4:]
    sep = "." if "." in s else (":" if ":" in s else "")
    if sep:
        ref, pin = s.split(sep, 1)
    else:
        ref, pin = s, ""
    fp = board.footprints.get(ref)
    if fp is None:
        return None, []
    if not pin:
        return fp, list(fp.pads)
    exact = [p for p in fp.pads if p.num == pin]
    if exact:
        return fp, exact
    by_net = [p for p in fp.pads if p.net == pin or p.net.endswith(f"/{pin}") or p.net.endswith(f"_{pin}")]
    return fp, by_net


def _check_loop(board: L.Board, item: dict, self_ref: str | None = None) -> tuple[str, str]:
    name = item.get("name") or f"{item.get('from')}->{item.get('to')}"
    frm = str(item.get("from", ""))
    to = str(item.get("to", ""))
    max_mm = float(item.get("max_mm", 0.0))
    same_layer = bool(item.get("same_layer", False))

    fp_a, pads_a = _resolve_pads(board, frm, self_ref)
    fp_b, pads_b = _resolve_pads(board, to, self_ref)
    if fp_a is None or not pads_a:
        return "FAIL", f"{name}: endpoint '{frm}' not found on board"
    if fp_b is None or not pads_b:
        return "FAIL", f"{name}: endpoint '{to}' not found on board"

    # Prefer pad pairs sharing a common non-empty net if both are multi-pad component specs
    shared = [
        (pa, pb)
        for pa in pads_a
        for pb in pads_b
        if pa.net and pa.net == pb.net
    ]
    pairs = shared if shared else [(pa, pb) for pa in pads_a for pb in pads_b]
    best_pa, best_pb = min(pairs, key=lambda ab: math.hypot(ab[0].x - ab[1].x, ab[0].y - ab[1].y))
    dist = math.hypot(best_pa.x - best_pb.x, best_pa.y - best_pb.y)

    problems = []
    if dist > max_mm + 1e-6:
        problems.append(f"distance {dist:.2f} mm > max {max_mm:.2f} mm ({best_pa.ref}.{best_pa.num} to {best_pb.ref}.{best_pb.num})")
    if same_layer and fp_a.layer != fp_b.layer:
        problems.append(f"layer mismatch ({fp_a.ref} on {fp_a.layer}, {fp_b.ref} on {fp_b.layer}; requires same_layer)")

    if problems:
        return "FAIL", f"{name}: {'; '.join(problems)}"
    return "PASS", f"{name}: {dist:.2f} mm <= {max_mm:.2f} mm ({best_pa.ref}.{best_pa.num}->{best_pb.ref}.{best_pb.num})"


def _seg_intersects_box(x0: float, y0: float, x1: float, y1: float, r: float, bx: L.Box) -> bool:
    eb = (bx[0] - r, bx[1] - r, bx[2] + r, bx[3] + r)
    if max(x0, x1) < eb[0] or min(x0, x1) > eb[2] or max(y0, y1) < eb[1] or min(y0, y1) > eb[3]:
        return False
    # Sample along segment for fast exact-enough rectangle hit test
    steps = max(4, int(math.hypot(x1 - x0, y1 - y0) / 0.1) + 1)
    for i in range(steps + 1):
        t = i / steps
        px = x0 + t * (x1 - x0)
        py = y0 + t * (y1 - y0)
        if eb[0] <= px <= eb[2] and eb[1] <= py <= eb[3]:
            return True
    return False


def _check_keepout(board: L.Board, item: dict, self_ref: str | None = None) -> tuple[str, str]:
    ref = item.get("ref") or self_ref
    if ref == "SELF" and self_ref:
        ref = self_ref
    name = item.get("name") or (f"keepout:{ref}" if ref else "keepout")
    fp = board.footprints.get(ref) if ref else None
    if ref and fp is None:
        return "FAIL", f"{name}: reference '{ref}' not found on board"

    raw_box = item.get("box")
    if raw_box is not None:
        b4 = (float(raw_box[0]), float(raw_box[1]), float(raw_box[2]), float(raw_box[3]))
        rel = item.get("relative")
        if fp is not None and (rel is True or (rel is None and max(abs(v) for v in b4) < 25.0)):
            kbox = L._rot_box((min(b4[0], b4[2]), min(b4[1], b4[3]), max(b4[0], b4[2]), max(b4[1], b4[3])), fp.x, fp.y, fp.rot)
        else:
            kbox = (min(b4[0], b4[2]), min(b4[1], b4[3]), max(b4[0], b4[2]), max(b4[1], b4[3]))
    elif fp is not None and fp.courtyard is not None:
        kbox = fp.courtyard
    else:
        return "FAIL", f"{name}: keepout requires 'box' or a footprint 'ref' with a courtyard"

    layers = set(item.get("layers") or ([fp.layer] if fp else ["F.Cu", "B.Cu"]))
    allow_nets = set(item.get("allow_nets") or [])
    allow_refs = set(item.get("allow_refs") or [])
    if ref:
        allow_refs.add(ref)

    no_copper = bool(item.get("no_copper", True if ("no_tracks" not in item and "no_vias" not in item) else False))
    no_tracks = bool(item.get("no_tracks", no_copper))
    no_vias = bool(item.get("no_vias", no_copper))

    hits: list[str] = []
    if no_tracks:
        for seg in board.segments:
            if seg.layer in layers and seg.net not in allow_nets:
                if _seg_intersects_box(seg.x0, seg.y0, seg.x1, seg.y1, seg.width / 2, kbox):
                    hits.append(f"track({seg.net or '?'} on {seg.layer} near ({(seg.x0+seg.x1)/2:.2f},{(seg.y0+seg.y1)/2:.2f}))")
                    if len(hits) >= 5:
                        break
    if no_vias:
        for v in board.vias:
            if v.net not in allow_nets:
                r = v.d / 2
                if kbox[0] - r <= v.x <= kbox[2] + r and kbox[1] - r <= v.y <= kbox[3] + r:
                    hits.append(f"via({v.net or '?'} at ({v.x:.2f},{v.y:.2f}))")
    if no_copper:
        for p in board.pads():
            if p.ref in allow_refs or p.net in allow_nets:
                continue
            if any(lay in layers or lay == "*.Cu" for lay in p.layers):
                pb = (p.x - p.w / 2, p.y - p.h / 2, p.x + p.w / 2, p.y + p.h / 2)
                if L.overlap(pb, kbox) > 0:
                    hits.append(f"pad({p.ref}.{p.num} at ({p.x:.2f},{p.y:.2f}))")

    if hits:
        return "FAIL", f"{name} [{kbox[0]:.2f},{kbox[1]:.2f}..{kbox[2]:.2f},{kbox[3]:.2f}]: violated by {', '.join(hits[:5])}"
    return "PASS", f"{name} [{kbox[0]:.2f},{kbox[1]:.2f}..{kbox[2]:.2f},{kbox[3]:.2f}] clean on {','.join(sorted(layers))}"


def _check_checklist_item(board: L.Board, item: dict, self_ref: str | None = None) -> tuple[str, str]:
    cid = str(item.get("id", "?"))
    rule = str(item.get("rule", ""))
    if item.get("from") and item.get("to") and item.get("max_mm") is not None:
        st, msg = _check_loop(board, {**item, "name": f"{cid} ({rule})"}, self_ref=self_ref)
        return st, msg
    if item.get("verified") is True:
        ev = f" — {item['evidence']}" if item.get("evidence") else ""
        return "PASS", f"{cid}: {rule}{ev}"
    return "FAIL", f"{cid}: unverified layout_checklist item ({rule})"


def collect_rules(project: Project, cfg: dict) -> tuple[list[tuple[str | None, dict]], list[tuple[str | None, dict]], list[tuple[str | None, dict]]]:
    loops: list[tuple[str | None, dict]] = []
    keepouts: list[tuple[str | None, dict]] = []
    checklist: list[tuple[str | None, dict]] = []

    pl = cfg.get("placement") or {}
    if isinstance(pl, dict):
        for x in pl.get("critical_loops") or []:
            loops.append((None, x))
        for x in pl.get("keepouts") or []:
            keepouts.append((None, x))
        for x in pl.get("layout_checklist") or []:
            checklist.append((None, x))
    for x in cfg.get("critical_loops") or []:
        loops.append((None, x))
    for x in cfg.get("keepouts") or []:
        keepouts.append((None, x))
    for x in cfg.get("layout_checklist") or []:
        checklist.append((None, x))

    cdir = project.constraints_dir
    if cdir.is_dir():
        for f in sorted(cdir.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            except OSError:
                continue
            if not isinstance(data, dict):
                continue
            refs = (data.get("match") or {}).get("refs") or []
            self_ref = refs[0] if len(refs) == 1 else None
            for x in data.get("critical_loops") or []:
                loops.append((self_ref, x))
            for x in data.get("keepouts") or []:
                keepouts.append((self_ref, x))
            for x in data.get("layout_checklist") or []:
                checklist.append((self_ref, x))
    return loops, keepouts, checklist


def check_pour_cfg(pcb: Path, pour_cfg: dict) -> tuple[str, str]:
    from ohmai.layout import pourgeom

    net = pour_cfg.get("net")
    layer = pour_cfg.get("layer")
    if not net or not layer:
        return "FAIL", "pour: 'net' and 'layer' are required in layout.yaml pour section"
    geom, pts, _ = pourgeom.extract_filled_pour(pcb, net, layer)
    if geom.is_empty:
        return "FAIL", f"pour {net} on {layer}: no filled_polygon found in {pcb.name}"
    board = L.load(pcb)
    local_pts: list[tuple[float, float]] = []
    lcfg_local = pour_cfg.get("local") or {}
    lrefs = lcfg_local.get("refs") or []
    margin = float(lcfg_local.get("margin", 2.0))
    if lrefs:
        boxes = [board.footprints[r].courtyard for r in lrefs if r in board.footprints and board.footprints[r].courtyard]
        if boxes:
            bx = (min(b[0] for b in boxes) - margin, min(b[1] for b in boxes) - margin,
                  max(b[2] for b in boxes) + margin, max(b[3] for b in boxes) + margin)
            local_pts = [(x, y) for x, y in pts if bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3]]
    rep = pourgeom.report(geom, pts, local_pts, pour_cfg)
    ok = pourgeom.verdict(rep, pour_cfg)
    xy_str = f" at ({rep['neck_xy'][0]:.2f}, {rep['neck_xy'][1]:.2f})" if rep.get("neck_xy") else ""
    msg = (f"pour {net} ({layer}): {rep['pieces']} piece(s), neck {rep['neck']:.2f} mm"
           f" (min {pour_cfg['min_neck']:.2f} mm){xy_str}, area {rep['area']:.1f} mm^2")
    return ("PASS" if ok else "FAIL"), msg


@register("placement", "Sub-second placement check: critical_loops pad-to-pad distances, keepouts, layout_checklist, and pour neck")
def placement_check(project: Project, opts: dict | None = None) -> Result:
    opts = opts or {}
    try:
        cfg = lcfg.load(project, opts.get("layout_config") and project.root / opts["layout_config"])
    except lcfg.ConfigError as e:
        return Result("placement", FAIL, f"layout config: {e}")

    loops, keepouts, checklist = collect_rules(project, cfg)
    pour_cfg = cfg.get("pour")
    if not loops and not keepouts and not checklist and not pour_cfg:
        return Result("placement", SKIP, "no critical_loops, keepouts, layout_checklist, or pour configured")

    pcb = Path(cfg["board"])
    if not pcb.is_file():
        return Result("placement", FAIL, f"{pcb.name}: board not found")

    board = L.load(pcb)
    findings: list[tuple[str, str, str]] = []
    for self_ref, item in loops:
        st, msg = _check_loop(board, item, self_ref)
        findings.append(("critical-loop", st, msg))
    for self_ref, item in keepouts:
        st, msg = _check_keepout(board, item, self_ref)
        findings.append(("keepout", st, msg))
    for self_ref, item in checklist:
        st, msg = _check_checklist_item(board, item, self_ref)
        findings.append(("layout-checklist", st, msg))
    if pour_cfg:
        st, msg = check_pour_cfg(pcb, pour_cfg)
        findings.append(("pour", st, msg))

    bad = [f for f in findings if f[1] == "FAIL"]
    details = [f"{st} [{kind}] {msg}" for kind, st, msg in findings]
    if bad:
        kinds = sorted({f[0] for f in bad})
        return Result("placement", FAIL, f"{len(bad)} failure(s) in {', '.join(kinds)}", details)
    return Result("placement", PASS, f"{len(findings)} placement/pour check(s) passed", details)

check_placement = placement_check
