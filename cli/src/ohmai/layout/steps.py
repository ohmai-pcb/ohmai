"""Execution helpers for `ohmai layout {fanout,autoroute,pour,handroute}`."""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

from .. import sexp
from . import dsn as dsn_mod
from . import grid as grid_mod
from . import placement as L
from . import pourgeom


def parse_timeout_sec(t_str: str) -> int:
    parts = [int(p) for p in str(t_str).strip().split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return int(parts[0])


def _same_net(a: str | None, b: str | None) -> bool:
    """True only when both nets are non-empty and equal. No-net never matches any net."""
    return bool(a) and bool(b) and a == b


def _class_for_net(rules: dict, net: str | None) -> dict:
    """Netclass width/clearance/via for `net` from rules_resolved; Default / top-level fallback.

    Empty / None nets resolve as Default (KiCad no-net has no class of its own).
    """
    net = net or ""
    classes = rules.get("classes") or {}
    patterns = rules.get("patterns") or []
    cname = grid_mod.netclass(patterns, net) if patterns else "Default"
    default = classes.get("Default") or {}
    cls = {
        "width": default.get("width", rules.get("track_w", 0.15)),
        "clearance": default.get("clearance", rules.get("clearance", 0.1)),
        "via_d": default.get("via_d", rules.get("via_d", 0.45)),
        "via_h": default.get("via_h", rules.get("via_h", 0.2)),
    }
    if cname in classes:
        for k, v in classes[cname].items():
            if v is not None:
                cls[k] = v
    return {
        "name": cname if cname in classes else "Default",
        "width": float(cls.get("width") or rules.get("track_w", 0.15)),
        "clearance": float(cls.get("clearance") or rules.get("clearance", 0.1)),
        "via_d": float(cls.get("via_d") or rules.get("via_d", 0.45)),
        "via_h": float(cls.get("via_h") or rules.get("via_h", 0.2)),
    }


def _pair_clearance(rules: dict, net_a: str | None, net_b: str | None) -> float:
    """KiCad-style clearance: max of the two netclasses' clearance values.

    None / "" (no-net) uses Default clearance via _class_for_net.
    """
    return max(_class_for_net(rules, net_a)["clearance"], _class_for_net(rules, net_b)["clearance"])


def _sort_handroute_nets(rules: dict, nets: list[str]) -> list[str]:
    """Thin-first for multi-net handroute: narrower netclass width first, then name.

    Wide power copper laid early can block later 0.6–0.8 mm nets; sorting ascending
    by track width (stable by name) lets thin nets claim corridors first.
    """
    return sorted(nets, key=lambda n: (_class_for_net(rules, n)["width"], n))


def _clamp_via_size(rules: dict, via_d: float, via_h: float) -> tuple[float, float]:
    """Prefer netclass via size, but never below board / .kicad_dru floors (or annular)."""
    min_d = float(rules.get("min_via_d") or 0.0)
    min_h = float(rules.get("min_via_h") or 0.0)
    min_ann = float(rules.get("min_via_annular") or 0.0)
    d = max(float(via_d), min_d) if min_d else float(via_d)
    h = max(float(via_h), min_h) if min_h else float(via_h)
    if min_ann and d < h + 2 * min_ann:
        d = h + 2 * min_ann
    if min_d:
        d = max(d, min_d)
    return d, h


def _thick_seg_box(x0: float, y0: float, x1: float, y1: float, half: float) -> tuple[float, float, float, float]:
    """Axis-aligned bbox of a thick segment (conservative vs true capsule)."""
    return (
        min(x0, x1) - half,
        min(y0, y1) - half,
        max(x0, x1) + half,
        max(y0, y1) + half,
    )


def _disk_box(x: float, y: float, r: float) -> tuple[float, float, float, float]:
    return (x - r, y - r, x + r, y + r)


def _zone_obstacles(pcb: Path) -> list[tuple[str, tuple[str, ...], tuple[float, float, float, float], bool]]:
    """Foreign-zone AABBs from the live board: (net, layers, box, keepout_no_via).

    Keepouts with vias not_allowed and copper zones (non-empty net) block fanout copper.
    Outline polygons only (filled_polygon not required) — conservative bbox.
    """
    try:
        tree = sexp.parse(pcb.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    out: list[tuple[str, tuple[str, ...], tuple[float, float, float, float], bool]] = []
    for z in sexp.find(tree, "zone"):
        nn = sexp.first(z, "net_name")
        net = str(nn[1]) if nn and len(nn) > 1 else ""
        layers_node = sexp.first(z, "layers") or sexp.first(z, "layer")
        layers: tuple[str, ...] = ()
        if layers_node and len(layers_node) > 1:
            layers = tuple(str(x) for x in layers_node[1:] if isinstance(x, str))
        keep = sexp.first(z, "keepout")
        keepout_no_via = False
        if keep is not None:
            vias_rule = sexp.first(keep, "vias")
            keepout_no_via = bool(vias_rule and len(vias_rule) > 1 and str(vias_rule[1]) == "not_allowed")
        if not keepout_no_via and not net:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for poly in list(sexp.find(z, "polygon")) + list(sexp.find(z, "filled_polygon")):
            pts = sexp.first(poly, "pts")
            if not pts:
                continue
            for xy in sexp.find(pts, "xy"):
                if len(xy) >= 3:
                    xs.append(float(xy[1]))
                    ys.append(float(xy[2]))
        if not xs:
            continue
        out.append((net, layers, (min(xs), min(ys), max(xs), max(ys)), keepout_no_via))
    return out


def _fanout_escape_ok(
    *,
    pad,
    vx: float,
    vy: float,
    via_d: float,
    stub_w: float,
    stub_layer: str,
    rules: dict,
    all_pads: list,
    segments: list,
    vias: list,
    zones: list,
    planned: list[dict],
    pad_gap: float,
) -> bool:
    """True if via + pad→via stub clear foreign copper (pads/tracks/vias/zones) by clearance.

    Same-net copper is allowed. Source pad is skipped. Full pad boxes (incl. large EPADs) are
    obstacles — never a centre-only point check. Prefer rejecting the candidate over a short.
    """
    v_r = via_d / 2
    stub_half = stub_w / 2
    v_box = _disk_box(vx, vy, v_r)
    stub_box = _thick_seg_box(pad.x, pad.y, vx, vy, stub_half)

    def _cl(other_net: str) -> float:
        # Max of pair clearance and fanout pad_gap so dense QFN toes still clear.
        return max(_pair_clearance(rules, pad.net, other_net or ""), pad_gap)

    # Foreign pads (full boxes, including EPAD). No-net is never same-net.
    for o in all_pads:
        if o is pad or _same_net(o.net, pad.net):
            continue
        # Layer-aware for stub; vias pierce all copper layers.
        stub_hit = (not o.layers) or stub_layer in o.layers or "*.Cu" in o.layers
        cl = _cl(o.net)
        if L.overlap(L.grow(v_box, cl), o.box, 0.0):
            return False
        if stub_hit and L.overlap(L.grow(stub_box, cl), o.box, 0.0):
            return False
        # Also require via copper itself stays pad_gap clear of the raw pad box.
        if L.overlap(L.grow(v_box, pad_gap), o.box, 0.0):
            return False

    # Existing tracks.
    for seg in segments:
        if _same_net(seg.net, pad.net):
            continue
        cl = _cl(seg.net)
        # Via hits every layer; stub only its layer.
        seg_half = seg.width / 2
        sbox = _thick_seg_box(seg.x0, seg.y0, seg.x1, seg.y1, seg_half)
        if L.overlap(L.grow(v_box, cl), sbox, 0.0):
            return False
        if seg.layer == stub_layer and L.overlap(L.grow(stub_box, cl), sbox, 0.0):
            return False

    # Existing vias.
    for v in vias:
        if _same_net(v.net, pad.net):
            continue
        cl = _cl(v.net)
        ob = _disk_box(v.x, v.y, v.size / 2)
        if L.overlap(L.grow(v_box, cl), ob, 0.0):
            return False
        if L.overlap(L.grow(stub_box, cl), ob, 0.0):
            return False

    # Zones / keepouts.
    for znet, zlayers, zbox, keepout_no_via in zones:
        if keepout_no_via:
            if L.overlap(v_box, zbox, 0.0) or L.overlap(stub_box, zbox, 0.0):
                return False
            continue
        if _same_net(znet, pad.net):
            continue
        cl = _cl(znet)
        layer_hit = (not zlayers) or stub_layer in zlayers or any(l.endswith(".Cu") for l in zlayers)
        if L.overlap(L.grow(v_box, cl), zbox, 0.0):
            return False
        if layer_hit and L.overlap(L.grow(stub_box, cl), zbox, 0.0):
            return False

    # Already-accepted fanout copper this call.
    for pv in planned:
        if _same_net(pv["net"], pad.net):
            continue
        cl = _cl(pv["net"])
        ob = _disk_box(pv["x"], pv["y"], float(pv["size"]) / 2)
        if L.overlap(L.grow(v_box, cl), ob, 0.0):
            return False
        if L.overlap(L.grow(stub_box, cl), ob, 0.0):
            return False
        # Their stub vs our via/stub.
        other_stub = _thick_seg_box(pv["pad_x"], pv["pad_y"], pv["x"], pv["y"], float(pv["width"]) / 2)
        other_layer = pv.get("layer", "F.Cu")
        if L.overlap(L.grow(v_box, cl), other_stub, 0.0):
            return False
        if other_layer == stub_layer and L.overlap(L.grow(stub_box, cl), other_stub, 0.0):
            return False
    return True


def run_fanout(cfg: dict, staged: Path | None = None, dry_run: bool = False) -> dict:
    pcb = Path(cfg["board"])
    if not pcb.is_file():
        raise FileNotFoundError(f"{pcb}: board not found")
    b = L.load(pcb)
    rules = cfg["rules_resolved"]
    fo = cfg["fanout"]
    via_d = float(rules["via_d"])
    via_h = float(rules["via_h"])
    hole_hole = float(rules["fanout"]["hole_hole"])
    pad_gap = float(rules["fanout"]["pad_gap"])
    edge_cl = float(rules["fanout"]["edge"])
    steps = [float(s) for s in (fo.get("steps") or [0.30, 0.45, 0.65, 0.9, 1.2])]
    angles = int(fo.get("angles") or 24)
    planes = set((cfg.get("planes") or {}).keys())

    # Materialize pads once: nested iteration over the generator would consume it and
    # skip foreign-pad collision checks for every escape after the first.
    all_pads = list(b.pads())
    existing_holes = [(v.x, v.y, v.drill / 2) for v in b.vias] + [
        (p.x, p.y, p.drill / 2) for p in all_pads if p.hole
    ]
    eb = b.edge_box
    zones = _zone_obstacles(pcb)

    escape_specs = fo.get("escape") or []
    target_refs = {spec["ref"] for spec in escape_specs if isinstance(spec, dict) and "ref" in spec}

    planned_vias: list[dict] = []
    skipped: list[dict] = []
    fo_track_fallback = float(fo.get("track_w") or 0.25)
    for p in all_pads:
        if p.hole or not p.net:
            continue
        if target_refs and p.ref not in target_refs:
            continue
        if not target_refs and p.net not in planes:
            continue
        # Already has a same-net via within 1.2 mm?
        if any(v.net == p.net and math.hypot(v.x - p.x, v.y - p.y) <= 1.2 for v in b.vias):
            continue
        nc = _class_for_net(rules, p.net)
        p_via_d, p_via_h = _clamp_via_size(rules, float(nc["via_d"]), float(nc["via_h"]))
        # Netclass width when a non-Default class matches; else fanout.track_w fallback.
        p_track_w = float(nc["width"]) if nc["name"] != "Default" else fo_track_fallback
        stub_layer = p.layers[0] if p.layers else "F.Cu"
        placed = None
        for r_step in steps:
            # Ring from full pad extent (EPAD / large pads), not a centre point.
            dist = max(p.w, p.h) / 2 + p_via_d / 2 + r_step
            for k in range(angles):
                ang = 2 * math.pi * k / angles
                vx = round(p.x + dist * math.cos(ang), 4)
                vy = round(p.y + dist * math.sin(ang), 4)
                if eb and (vx - p_via_d / 2 < eb[0] + edge_cl or vx + p_via_d / 2 > eb[2] - edge_cl or
                           vy - p_via_d / 2 < eb[1] + edge_cl or vy + p_via_d / 2 > eb[3] - edge_cl):
                    continue
                if any(math.hypot(vx - hx, vy - hy) < (p_via_h / 2 + hr + hole_hole) for hx, hy, hr in existing_holes):
                    continue
                if not _fanout_escape_ok(
                    pad=p, vx=vx, vy=vy, via_d=p_via_d, stub_w=p_track_w, stub_layer=stub_layer,
                    rules=rules, all_pads=all_pads, segments=b.segments, vias=b.vias, zones=zones,
                    planned=planned_vias, pad_gap=pad_gap,
                ):
                    continue
                # Require pad copper connection: always carry stub endpoints (no orphan vias).
                placed = {"ref": p.ref, "pad": p.num, "net": p.net, "x": vx, "y": vy,
                          "pad_x": p.x, "pad_y": p.y, "layer": stub_layer,
                          "size": p_via_d, "drill": p_via_h, "width": p_track_w}
                existing_holes.append((vx, vy, p_via_h / 2))
                break
            if placed:
                break
        if placed and "pad_x" in placed and "pad_y" in placed:
            planned_vias.append(placed)
        else:
            # No legal escape — skip rather than plant a short / illegal via.
            skipped.append({"ref": p.ref, "pad": p.num, "net": p.net, "reason": "no_legal_escape"})

    target = staged or (pcb if not dry_run else None)
    if target is not None:
        # Wipe-or-replace: always rebuild staged from the live board + this fanout only.
        # Prefer writing zero fanout copper over illegal copper when every pad was skipped.
        _append_copper_to_pcb(pcb, target, planned_vias, [], via_d, via_h, fo_track_fallback)
        if staged is not None:
            _ensure_staged_project_files(pcb, Path(staged))

    return {
        "board": str(pcb),
        "staged": str(staged) if staged else None,
        "dry_run": dry_run,
        "vias_added": len(planned_vias),
        "vias": planned_vias,
        "skipped": skipped,
        "skipped_count": len(skipped),
    }


def _ensure_staged_project_files(board: Path, staged: Path) -> None:
    """Copy .kicad_pro / .kicad_dru next to a staged PCB so DRC picks up project design rules.

    Staged boards live under work_dir (often out/layout/), away from the live board's project
    files. KiCad binds rules by *basename* (staged.kicad_pcb ↔ staged.kicad_pro), so copies are
    renamed to the staged stem — keeping the live board's name leaves KiCad on default via mins
    (0.5 / 0.3) and false via_diameter / drill_out_of_range.
    """
    src_dir = Path(board).resolve().parent
    staged_p = Path(staged).resolve()
    dst_dir = staged_p.parent
    stem = staged_p.stem
    dst_dir.mkdir(parents=True, exist_ok=True)
    board_stem = Path(board).stem
    for ext in (".kicad_pro", ".kicad_dru"):
        src = src_dir / f"{board_stem}{ext}"
        if not src.is_file():
            # Fall back to any single matching file in the board dir.
            matches = list(src_dir.glob(f"*{ext}"))
            src = matches[0] if len(matches) == 1 else None
        if src is None or not Path(src).is_file():
            continue
        dst = dst_dir / f"{stem}{ext}"
        if dst.resolve() == Path(src).resolve():
            continue
        shutil.copy2(src, dst)


def _fanout_vias_from_staged(live_pcb: Path, staged_pcb: Path) -> list[dict]:
    """Fanout via+stub copper present on staged but not on live (for wipe-or-replace chaining).

    Identifies vias that are missing from live and have a same-net stub segment from a pad
    centre to the via. Mid-route handroute vias (no pad stub) are not returned here.
    """
    live = L.load(live_pcb)
    st = L.load(staged_pcb)
    live_via_pos = {(round(v.x, 4), round(v.y, 4)) for v in live.vias}
    pads = list(live.pads())
    out: list[dict] = []
    for v in st.vias:
        key = (round(v.x, 4), round(v.y, 4))
        if key in live_via_pos:
            continue
        stub = None
        for seg in st.segments:
            if seg.net != v.net:
                continue
            ends = [((seg.x0, seg.y0), (seg.x1, seg.y1)), ((seg.x1, seg.y1), (seg.x0, seg.y0))]
            for (ex, ey), (ox, oy) in ends:
                if math.hypot(ex - v.x, ey - v.y) > 0.05:
                    continue
                for p in pads:
                    if p.net != v.net:
                        continue
                    if math.hypot(ox - p.x, oy - p.y) <= 0.05:
                        stub = (p, seg)
                        break
                if stub:
                    break
            if stub:
                break
        if not stub:
            continue
        p, seg = stub
        out.append({
            "ref": p.ref,
            "pad": p.num,
            "net": v.net,
            "x": v.x,
            "y": v.y,
            "pad_x": p.x,
            "pad_y": p.y,
            "layer": seg.layer or (p.layers[0] if p.layers else "F.Cu"),
            "size": v.size,
            "drill": v.drill,
            "width": seg.width,
        })
    return out


def _kept_handroute_from_staged(
    live_pcb: Path, staged_pcb: Path, rip_nets: set[str],
) -> tuple[list[dict], list[dict]]:
    """Prior handroute copper on staged for nets outside rip_nets (incremental --nets).

    Re-running the same nets replaces their copper (no doubling). Other nets' staged tracks
    and mid-route vias are carried forward onto the wipe-or-replace write from live.
    """
    live = L.load(live_pcb)
    st = L.load(staged_pcb)
    live_seg = {
        (s.net, s.layer, round(s.x0, 4), round(s.y0, 4), round(s.x1, 4), round(s.y1, 4), round(s.width, 4))
        for s in live.segments
    }
    live_via = {(round(v.x, 4), round(v.y, 4), v.net) for v in live.vias}
    pads = list(live.pads())
    # Fanout stubs (pad→via) are carried separately; skip them here.
    fanout_stub_keys = set()
    for v in st.vias:
        for seg in st.segments:
            if seg.net != v.net:
                continue
            for (ex, ey), (ox, oy) in [
                ((seg.x0, seg.y0), (seg.x1, seg.y1)),
                ((seg.x1, seg.y1), (seg.x0, seg.y0)),
            ]:
                if math.hypot(ex - v.x, ey - v.y) > 0.05:
                    continue
                if any(p.net == v.net and math.hypot(ox - p.x, oy - p.y) <= 0.05 for p in pads):
                    fanout_stub_keys.add((
                        seg.net, seg.layer,
                        round(seg.x0, 4), round(seg.y0, 4),
                        round(seg.x1, 4), round(seg.y1, 4),
                        round(seg.width, 4),
                    ))
    tracks: list[dict] = []
    for s in st.segments:
        if s.net in rip_nets:
            continue
        key = (s.net, s.layer, round(s.x0, 4), round(s.y0, 4), round(s.x1, 4), round(s.y1, 4), round(s.width, 4))
        if key in live_seg or key in fanout_stub_keys:
            continue
        tracks.append({
            "net": s.net, "layer": s.layer,
            "x0": s.x0, "y0": s.y0, "x1": s.x1, "y1": s.y1, "width": s.width,
        })
    vias: list[dict] = []
    for v in st.vias:
        if v.net in rip_nets:
            continue
        key = (round(v.x, 4), round(v.y, 4), v.net)
        if key in live_via:
            continue
        # Skip fanout vias (have pad stubs) — those come from _fanout_vias_from_staged.
        is_fanout = False
        for seg in st.segments:
            if seg.net != v.net:
                continue
            for (ex, ey), (ox, oy) in [
                ((seg.x0, seg.y0), (seg.x1, seg.y1)),
                ((seg.x1, seg.y1), (seg.x0, seg.y0)),
            ]:
                if math.hypot(ex - v.x, ey - v.y) > 0.05:
                    continue
                if any(p.net == v.net and math.hypot(ox - p.x, oy - p.y) <= 0.05 for p in pads):
                    is_fanout = True
                    break
            if is_fanout:
                break
        if is_fanout:
            continue
        vias.append({
            "net": v.net, "x": v.x, "y": v.y, "size": v.size, "drill": v.drill,
        })
    return vias, tracks


def _append_copper_to_pcb(
    src_pcb: Path,
    dst_pcb: Path,
    vias: list[dict],
    tracks: list[dict],
    via_d: float,
    via_h: float,
    default_w: float,
) -> None:
    tree = sexp.parse(src_pcb.read_text(encoding="utf-8", errors="replace"))
    net_map: dict[str, int] = {}
    for n in sexp.find(tree, "net"):
        if len(n) >= 3:
            net_map[str(n[2])] = int(n[1])
    for v in vias:
        nid = net_map.get(v["net"], 0)
        tree.append([
            "via",
            ["at", str(v["x"]), str(v["y"])],
            ["size", str(v.get("size", via_d))],
            ["drill", str(v.get("drill", via_h))],
            ["layers", "F.Cu", "B.Cu"],
            ["net", str(nid)],
        ])
        # Fanout vias carry pad_x/pad_y and need a stub. Mid-route handroute vias do not —
        # connecting tracks in `tracks` (or already on src) keep them from dangling.
        if "pad_x" in v and "pad_y" in v:
            tree.append([
                "segment",
                ["start", str(v["pad_x"]), str(v["pad_y"])],
                ["end", str(v["x"]), str(v["y"])],
                ["width", str(v.get("width", default_w))],
                ["layer", v.get("layer", "F.Cu")],
                ["net", str(nid)],
            ])
    for t in tracks:
        nid = net_map.get(t["net"], 0)
        tree.append([
            "segment",
            ["start", str(t["x0"]), str(t["y0"])],
            ["end", str(t["x1"]), str(t["y1"])],
            ["width", str(t.get("width", default_w))],
            ["layer", t.get("layer", "F.Cu")],
            ["net", str(nid)],
        ])
    dst_pcb.parent.mkdir(parents=True, exist_ok=True)
    dst_pcb.write_text(sexp.dumps(tree) + "\n", encoding="utf-8")


def prepare_autoroute(
    cfg: dict,
    dsn_in: Path | None = None,
    passes: int | None = None,
    opt_passes: int | None = None,
    timeout: str | None = None,
    via_costs: int | None = None,
    threads: int | None = None,
) -> dict:
    ar = cfg["autoroute"]
    work = Path(cfg["work_dir"])
    work.mkdir(parents=True, exist_ok=True)
    pcb = Path(cfg["board"])
    raw_dsn = dsn_in or (work / f"{pcb.stem}-raw.dsn")
    patched_dsn = work / f"{pcb.stem}.dsn"
    ses_out = work / f"{pcb.stem}.ses"

    eff_passes = int(passes if passes is not None else ar.get("passes", 30))
    eff_opt = int(opt_passes if opt_passes is not None else ar.get("opt_passes", 2))
    eff_timeout = str(timeout if timeout is not None else ar.get("timeout", "00:10:00"))
    eff_via_costs = int(via_costs if via_costs is not None else ar.get("via_costs", 50))
    eff_threads = threads if threads is not None else ar.get("threads")

    dsn_edited = False
    if raw_dsn.is_file():
        tree = dsn_mod.parse(raw_dsn.read_text(encoding="utf-8", errors="replace"))
        dsn_cfg = dict(ar.get("dsn") or {})
        dsn_cfg["planes"] = cfg.get("planes") or {}
        dsn_cfg["inner_nets"] = ar.get("inner_nets")
        dsn_cfg["signal_layers"] = cfg.get("signal_layers") or ["F.Cu", "B.Cu"]
        dsn_cfg["inner_signal_layer"] = cfg.get("inner_signal_layer")
        dsn_mod.edit_dsn(tree, dsn_cfg)
        patched_dsn.write_text(dsn_mod.dumps(tree), encoding="utf-8")
        dsn_edited = True

    cmd = [
        "java",
        "-Djava.awt.headless=true",
        "-jar",
        "freerouting.jar",
        "-de",
        str(patched_dsn if dsn_edited else raw_dsn),
        "-do",
        str(ses_out),
        "-mp",
        str(eff_passes),
        "--Router.max_passes=" + str(eff_passes),
        "--Router.optimizer_max_passes=" + str(eff_opt),
        "--Router.job_timeout=" + eff_timeout,
        "--Router.via_costs=" + str(eff_via_costs),
        "--gui.enabled=false",
    ]
    if eff_threads:
        cmd.extend(["-mt", str(eff_threads)])

    return {
        "board": str(pcb),
        "raw_dsn": str(raw_dsn),
        "patched_dsn": str(patched_dsn),
        "dsn_edited": dsn_edited,
        "ses_out": str(ses_out),
        "passes": eff_passes,
        "opt_passes": eff_opt,
        "timeout": eff_timeout,
        "timeout_sec": parse_timeout_sec(eff_timeout),
        "via_costs": eff_via_costs,
        "cmd": cmd,
    }


def run_pour(cfg: dict, net: str | None = None, layer: str | None = None, staged: Path | None = None) -> dict:
    src_pcb = Path(cfg["board"])
    pcb = staged or src_pcb
    if staged and not pcb.is_file() and src_pcb.is_file():
        pcb.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_pcb, pcb)
    if not pcb.is_file():
        raise FileNotFoundError(f"{pcb}: board not found")
    pour_cfg = dict(lcfg_pour(cfg, net, layer))
    pnet = pour_cfg.get("net")
    player = pour_cfg.get("layer")
    if not pnet or not player:
        raise ValueError("pour net and layer must be specified via layout.yaml pour: or --net / --layer")
    geom, pts, _ = pourgeom.extract_filled_pour(pcb, pnet, player)
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
    return {
        "board": str(pcb),
        "net": pnet,
        "layer": player,
        "min_neck": pour_cfg["min_neck"],
        "local_neck": pour_cfg["local_neck"],
        **rep,
        "ok": ok,
    }


def lcfg_pour(cfg: dict, net: str | None, layer: str | None) -> dict:
    from . import config as lcfg

    base = dict(lcfg.POUR_DEFAULTS)
    if cfg.get("pour"):
        base.update(cfg["pour"])
    if net:
        base["net"] = net
    if layer:
        base["layer"] = layer
    return base


def _handroute_copper_items(board, net_name: str, layers: list[str]) -> list:
    """Same-net geometry for copper_island; Board currently exposes no filled zones.

    Match the placement model: pads are their (possibly rotated) bounding boxes,
    tracks are round-ended strokes, and vias span all modeled copper layers.
    Include non-routing copper in connectivity, but not as A* landing cells.
    """
    from shapely.geometry import LineString, Point, box

    # Routing layers retain their A* indices. Extra copper layers participate in
    # island connectivity only (e.g. two fanout vias joined by an inner track).
    other_layers = {s.layer for s in board.segments if s.layer.endswith(".Cu")}
    other_layers.update(lay for p in board.pads() for lay in p.layers
                        if lay.endswith(".Cu") and lay != "*.Cu")
    copper_layers = layers + sorted(other_layers - set(layers))
    items = []
    for p in board.pads():
        if _same_net(p.net, net_name):
            shapes = {
                li: box(*p.box) for li, lay in enumerate(copper_layers)
                if lay in p.layers or "*.Cu" in p.layers
            }
            if shapes:
                items.append((net_name, shapes))
    for v in board.vias:
        if _same_net(v.net, net_name):
            disk = Point(v.x, v.y).buffer(v.size / 2)
            items.append((net_name, dict.fromkeys(range(len(copper_layers)), disk)))
    for s in board.segments:
        if _same_net(s.net, net_name) and s.layer in copper_layers:
            stroke = LineString([(s.x0, s.y0), (s.x1, s.y1)]).buffer(s.width / 2)
            items.append((net_name, {copper_layers.index(s.layer): stroke}))
    return items


def _handroute_islands(items: list) -> list[list[int]]:
    """Partition touching copper using the existing geometric connectivity helper."""
    remaining = set(range(len(items)))
    islands = []
    while remaining:
        island = grid_mod.copper_island(items, min(remaining))
        islands.append(island)
        remaining.difference_update(island)
    return islands


def _handroute_terminal_cells(items: list, island: list[int], g, free: dict) -> set:
    """Rasterize copper centres, never overriding inflated foreign-copper obstacles.

    Own copper was never blocked. Filtering by free also avoids seeding A* inside
    a foreign clearance envelope (unblocking such a cell can manufacture a short).
    """
    import numpy as np
    from shapely import intersects_xy

    cells = set()
    for i in island:
        for li, shape in items[i][1].items():
            if li not in free:
                continue
            x0, y0, x1, y1 = shape.bounds
            ix0, iy0 = g.cell(x0, y0)
            ix1, iy1 = g.cell(x1, y1)
            xa, xb = max(0, ix0), min(g.nx - 1, ix1)
            ya, yb = max(0, iy0), min(g.ny - 1, iy1)
            if xa > xb or ya > yb:
                continue
            ys, xs = np.mgrid[ya:yb + 1, xa:xb + 1]
            mask = intersects_xy(shape, g.x0 + (xs + 0.5) * g.res,
                                 g.y0 + (ys + 0.5) * g.res)
            mask &= free[li][ya:yb + 1, xa:xb + 1]
            cells.update((li, int(y), int(x)) for y, x in zip(ys[mask], xs[mask], strict=True))
    return cells


def run_handroute(
    cfg: dict,
    action: str = "status",
    nets: list[str] | None = None,
    staged: Path | None = None,
    dry_run: bool = False,
) -> dict:
    pcb = Path(cfg["board"])
    base_path = Path(cfg["base"])
    routes_path = Path(cfg["routes"])

    if action == "status":
        board = L.load(pcb) if pcb.is_file() else None
        routes_data = json.loads(routes_path.read_text(encoding="utf-8")) if routes_path.is_file() else {}
        return {
            "action": "status",
            "board": str(pcb),
            "base_exists": base_path.is_file(),
            "routes_exists": routes_path.is_file(),
            "segments_on_board": len(board.segments) if board else 0,
            "vias_on_board": len(board.vias) if board else 0,
            "hand_routed_nets": sorted((routes_data.get("routes") or {}).keys()) if isinstance(routes_data, dict) else [],
        }

    if action == "freeze":
        if not pcb.is_file():
            raise FileNotFoundError(f"{pcb}: board not found")
        board = L.load(pcb)
        snap = {
            "segments": [
                {"net": s.net, "layer": s.layer, "x0": s.x0, "y0": s.y0, "x1": s.x1, "y1": s.y1, "width": s.width}
                for s in board.segments
            ],
            "vias": [
                {"net": v.net, "x": v.x, "y": v.y, "size": v.size, "drill": v.drill}
                for v in board.vias
            ],
        }
        if not dry_run:
            base_path.parent.mkdir(parents=True, exist_ok=True)
            base_path.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
        return {"action": "freeze", "base": str(base_path), "segments": len(snap["segments"]), "vias": len(snap["vias"])}

    if action == "apply":
        # Promote staged copper onto the live board. Do not silently succeed with an empty no-op.
        src = staged or (Path(cfg["work_dir"]) / "staged.kicad_pcb")
        if not src.is_file():
            raise FileNotFoundError(f"{src}: staged board not found (run fanout/handroute --staged first)")
        if src.resolve() == pcb.resolve():
            raise ValueError("handroute apply: staged path is the live board; refusing no-op promote")
        # Cheap validity: must parse as kicad_pcb and contain at least one via or segment.
        tree = sexp.parse(src.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(tree, list) or not tree or tree[0] != "kicad_pcb":
            raise ValueError(f"{src}: not a kicad_pcb")
        n_via = sum(1 for _ in sexp.walk(tree, "via"))
        n_seg = sum(1 for _ in sexp.walk(tree, "segment"))
        if n_via + n_seg == 0:
            raise ValueError(f"{src}: staged board has no vias/segments to apply")
        if not dry_run:
            pcb.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, pcb)
        return {
            "action": "apply",
            "board": str(pcb),
            "staged": str(src),
            "dry_run": dry_run,
            "routed_nets": [],
            "segments_added": n_seg,
            "vias_on_staged": n_via,
            "promoted": not dry_run,
            "routes": {},
        }

    if action == "route":
        if not pcb.is_file():
            raise FileNotFoundError(f"{pcb}: board not found")
        # Obstacle board: see fanout copper on an existing staged file when chaining.
        # Write base is always the live board (wipe-or-replace); fanout vias are re-carried.
        route_src = pcb
        carry_fanout: list[dict] = []
        keep_vias: list[dict] = []
        keep_tracks: list[dict] = []
        target_nets = nets or []
        if not target_nets:
            raise ValueError("handroute route requires --nets (refusing empty success)")
        if staged is not None and Path(staged).is_file() and Path(staged).resolve() != pcb.resolve():
            route_src = Path(staged)
            carry_fanout = _fanout_vias_from_staged(pcb, Path(staged))
            keep_vias, keep_tracks = _kept_handroute_from_staged(pcb, Path(staged), set(target_nets))
        # Route against exactly the copper that survives the wipe-or-replace write.
        # Old target-net staged tracks must not become terminals: they are removed.
        board = L.load(pcb)
        if route_src != pcb:
            for v in carry_fanout + keep_vias:
                board.vias.append(L.Via(v["x"], v["y"], v["size"], v["net"], v["drill"]))
            for v in carry_fanout:
                board.segments.append(L.Track(v["net"], v["layer"], v["pad_x"], v["pad_y"],
                                             v["x"], v["y"], v["width"]))
            board.segments.extend(L.Track(**s) for s in keep_tracks)
        rules = cfg["rules_resolved"]
        # Thin-first: narrower class before wider so 1.0 mm BATT/STEREO does not block later thin nets.
        target_nets = _sort_handroute_nets(rules, list(target_nets))
        res = float((cfg.get("grid") or {}).get("res", 0.05))
        eb = board.edge_box or (95.0, 95.0, 125.0, 155.0)
        layers = list(cfg.get("signal_layers") or ["F.Cu", "B.Cu"])
        routed: dict[str, list[dict]] = {}
        remaining_islands: dict[str, int] = {}
        # Copper laid earlier in this call — later nets must clear it.
        fresh_tracks: list[dict] = []
        fresh_vias: list[dict] = []
        import numpy as np

        for net_name in target_nets:
            items = _handroute_copper_items(board, net_name, layers)
            islands = _handroute_islands(items)
            remaining_islands[net_name] = len(islands)
            if len(islands) < 2:
                continue  # All exposed copper (including every pad) is already connected.
            bounds = [shape.bounds for _, shapes in items for shape in shapes.values()]
            nc = _class_for_net(rules, net_name)
            track_w = float(nc["width"])
            margin = float((cfg.get("handroute") or {}).get("search_margin", 8.0))
            bbox = (
                max(eb[0], min(b[0] for b in bounds) - margin),
                max(eb[1], min(b[1] for b in bounds) - margin),
                min(eb[2], max(b[2] for b in bounds) + margin),
                min(eb[3], max(b[3] for b in bounds) + margin),
            )
            # Grid.around covers the bbox; free/viaok arrays carry per-layer occupancy.
            g = grid_mod.Grid.around(bbox, res)
            if g.nx < 1 or g.ny < 1:
                continue
            layer_idxs = list(range(len(layers)))
            free = {li: np.ones((g.ny, g.nx), dtype=bool) for li in layer_idxs}
            viaok = np.ones((g.ny, g.nx), dtype=bool)
            via_d_net = float(nc["via_d"])

            def _block_rect(li: int, x0: float, y0: float, x1: float, y1: float) -> None:
                ix0, iy0 = g.cell(x0, y0)
                ix1, iy1 = g.cell(x1, y1)
                xa, xb = sorted((ix0, ix1))
                ya, yb = sorted((iy0, iy1))
                xa, xb = max(0, xa), min(g.nx - 1, xb)
                ya, yb = max(0, ya), min(g.ny - 1, yb)
                if xa <= xb and ya <= yb:
                    free[li][ya : yb + 1, xa : xb + 1] = False

            def _block_viaok(x0: float, y0: float, x1: float, y1: float) -> None:
                ix0, iy0 = g.cell(x0, y0)
                ix1, iy1 = g.cell(x1, y1)
                xa, xb = sorted((ix0, ix1))
                ya, yb = sorted((iy0, iy1))
                xa, xb = max(0, xa), min(g.nx - 1, xb)
                ya, yb = max(0, ya), min(g.ny - 1, yb)
                if xa <= xb and ya <= yb:
                    viaok[ya : yb + 1, xa : xb + 1] = False

            def _block_disk(li: int, x: float, y: float, r: float) -> None:
                _block_rect(li, x - r, y - r, x + r, y + r)

            def _block_thick_seg(li: int, x0: float, y0: float, x1: float, y1: float, half: float) -> None:
                length = math.hypot(x1 - x0, y1 - y0)
                n = max(1, int(math.ceil(length / max(res * 0.5, 1e-6))))
                for i in range(n + 1):
                    t = i / n
                    _block_disk(li, x0 + t * (x1 - x0), y0 + t * (y1 - y0), half)

            # Inflate obstacles by clearance + half our track width so wide power tracks don't short.
            # Every copper pad blocks unless it is the same non-empty net being routed.
            # No-net / empty / None pads are never same-net terminals and always obstruct.
            for op in board.pads():
                if _same_net(op.net, net_name):
                    continue
                grow = _pair_clearance(rules, net_name, op.net) + track_w / 2
                for li, lay in enumerate(layers):
                    # Empty layer list: treat as all copper (conservative; no-net EPADs must block).
                    if (not op.layers) or lay in op.layers or "*.Cu" in op.layers:
                        _block_rect(
                            li,
                            op.x - op.w / 2 - grow,
                            op.y - op.h / 2 - grow,
                            op.x + op.w / 2 + grow,
                            op.y + op.h / 2 + grow,
                        )
                # Layer-change vias must not land in/on foreign / no-net pads (incl. EPADs).
                via_grow = _pair_clearance(rules, net_name, op.net) + via_d_net / 2
                _block_viaok(
                    op.box[0] - via_grow,
                    op.box[1] - via_grow,
                    op.box[2] + via_grow,
                    op.box[3] + via_grow,
                )
            for seg in board.segments:
                if _same_net(seg.net, net_name):
                    continue
                if seg.layer not in layers:
                    continue
                li = layers.index(seg.layer)
                grow = seg.width / 2 + _pair_clearance(rules, net_name, seg.net) + track_w / 2
                _block_thick_seg(li, seg.x0, seg.y0, seg.x1, seg.y1, grow)
            for v in board.vias:
                if _same_net(v.net, net_name):
                    continue
                grow = v.size / 2 + _pair_clearance(rules, net_name, v.net) + track_w / 2
                for li in layer_idxs:
                    _block_disk(li, v.x, v.y, grow)
            for seg in fresh_tracks:
                if _same_net(seg["net"], net_name):
                    continue
                if seg["layer"] not in layers:
                    continue
                li = layers.index(seg["layer"])
                grow = float(seg["width"]) / 2 + _pair_clearance(rules, net_name, seg["net"]) + track_w / 2
                _block_thick_seg(li, seg["x0"], seg["y0"], seg["x1"], seg["y1"], grow)

            for v in fresh_vias:
                if _same_net(v["net"], net_name):
                    continue
                grow = float(v["size"]) / 2 + _pair_clearance(rules, net_name, v["net"]) + track_w / 2
                for li in layer_idxs:
                    _block_disk(li, v["x"], v["y"], grow)

            # Include whole islands, not just pad centres. A via contributes all
            # its layers, so a fanout stub offers an escape beyond a boxed-in pad.
            terminals = [_handroute_terminal_cells(items, island, g, free) for island in islands]
            hr = cfg.get("handroute") or {}
            via_cost = float(hr.get("via_cost", 1.6))
            pitch = grid_mod.via_pitch_cells(float(nc["via_h"]), float(rules.get("hole_hole", rules.get("hole_to_hole", 0.25))), res)
            cost_mult = {li: (1.0 if li == 0 else float((hr.get("inner") or {}).get("cost", 4.0))) for li in layer_idxs}
            paths = []
            # A* can reach ANY other island. If the first island cannot escape,
            # try the others; one blocked first pair must not hide useful copper.
            # Merge successful connections and keep growing until no pair routes.
            while len(terminals) > 1:
                for i, starts in enumerate(terminals):
                    goals = set().union(*(t for j, t in enumerate(terminals) if j != i))
                    path = grid_mod.astar(
                        free, viaok, starts, goals, layer_idxs, cost_mult, res,
                        via_cost, pitch, max_expand=int(hr.get("max_expand", 2_500_000)),
                    )
                    if not path:
                        continue
                    paths.append(path)
                    path_cells = set(path)
                    joined = {j for j, t in enumerate(terminals) if path_cells & t} | {i}
                    merged = path_cells.union(*(terminals[j] for j in joined))
                    terminals = [t for j, t in enumerate(terminals) if j not in joined] + [merged]
                    # Subsequent branches must respect vias already placed here.
                    for before, after in zip(path, path[1:], strict=False):
                        if before[0] != after[0]:
                            yy, xx = np.ogrid[:g.ny, :g.nx]
                            viaok[(yy - before[1]) ** 2 + (xx - before[2]) ** 2 < pitch ** 2] = False
                    break
                else:
                    break
            remaining_islands[net_name] = len(terminals)
            segs = []
            net_vias = []
            for path in paths:
                runs = grid_mod.simplify(path, g.xy)
                seg_pairs, via_pts = grid_mod.runs_to_segments(runs)
                # NOTE: do not name the endpoint `b` — that shadowed the Board and crashed on net 2+.
                for l0, a, bpt in seg_pairs:
                    segs.append({
                        "net": net_name,
                        "layer": layers[l0],
                        "x0": round(a[0], 4),
                        "y0": round(a[1], 4),
                        "x1": round(bpt[0], 4),
                        "y1": round(bpt[1], 4),
                        "width": track_w,
                    })
                # Layer-change vias from runs_to_segments must be written (not discarded).
                for pt in via_pts:
                    net_vias.append({
                        "net": net_name,
                        "x": round(pt[0], 4),
                        "y": round(pt[1], 4),
                        "size": float(nc["via_d"]),
                        "drill": float(nc["via_h"]),
                    })
            if segs or net_vias:
                routed[net_name] = segs
                fresh_tracks.extend(segs)
                fresh_vias.extend(net_vias)

        fresh_track_list = [s for segs in routed.values() for s in segs]
        all_tracks = list(keep_tracks) + fresh_track_list
        all_vias = list(carry_fanout) + list(keep_vias) + list(fresh_vias)
        dst = staged or (pcb if not dry_run else None)
        if dst is not None:
            # Wipe-or-replace from live: fanout + kept sibling handroute + this call's copper.
            # Re-routed nets are not in keep_* so they cannot double on a second --staged run.
            if all_tracks or all_vias:
                _append_copper_to_pcb(
                    pcb, dst, all_vias, all_tracks,
                    float(rules["via_d"]), float(rules["via_h"]), float(rules["track_w"]),
                )
            elif staged and Path(staged).resolve() != pcb.resolve():
                shutil.copy2(pcb, staged)
            if staged is not None:
                _ensure_staged_project_files(pcb, Path(staged))
        return {
            "action": action,
            "board": str(pcb),
            "staged": str(staged) if staged else None,
            "dry_run": dry_run,
            "routed_nets": sorted(routed.keys()),
            "remaining_islands": remaining_islands,
            "segments_added": len(fresh_track_list),
            "vias_added": len(fresh_vias),
            "fanout_carried": len(carry_fanout),
            "routes": routed,
        }

    raise ValueError(f"unknown handroute action '{action}'")
