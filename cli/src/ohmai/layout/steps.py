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


def _class_for_net(rules: dict, net: str) -> dict:
    """Netclass width/clearance/via for `net` from rules_resolved; Default / top-level fallback."""
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


def _pair_clearance(rules: dict, net_a: str, net_b: str) -> float:
    """KiCad-style clearance: max of the two netclasses' clearance values."""
    return max(_class_for_net(rules, net_a)["clearance"], _class_for_net(rules, net_b)["clearance"])


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

    escape_specs = fo.get("escape") or []
    target_refs = {spec["ref"] for spec in escape_specs if isinstance(spec, dict) and "ref" in spec}

    planned_vias: list[dict] = []
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
        p_via_d = float(nc["via_d"])
        p_via_h = float(nc["via_h"])
        # Netclass width when a non-Default class matches; else fanout.track_w fallback.
        p_track_w = float(nc["width"]) if nc["name"] != "Default" else fo_track_fallback
        placed = None
        for r_step in steps:
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
                v_box = (vx - p_via_d / 2, vy - p_via_d / 2, vx + p_via_d / 2, vy + p_via_d / 2)
                # Foreign pads (other nets, including empty-net copper) are obstacles for via cells.
                if any(
                    o.net != p.net
                    and L.overlap(L.grow(v_box, pad_gap), o.box, 0.0)
                    for o in all_pads
                ):
                    continue
                # Require pad copper connection: always carry stub endpoints (no orphan vias).
                placed = {"ref": p.ref, "pad": p.num, "net": p.net, "x": vx, "y": vy,
                          "pad_x": p.x, "pad_y": p.y, "layer": p.layers[0] if p.layers else "F.Cu",
                          "size": p_via_d, "drill": p_via_h, "width": p_track_w}
                existing_holes.append((vx, vy, p_via_h / 2))
                break
            if placed:
                break
        if placed and "pad_x" in placed and "pad_y" in placed:
            planned_vias.append(placed)

    target = staged or (pcb if not dry_run else None)
    if target is not None:
        # Wipe-or-replace: always rebuild staged from the live board + this fanout only.
        _append_copper_to_pcb(pcb, target, planned_vias, [], via_d, via_h, fo_track_fallback)
        if staged is not None:
            _ensure_staged_project_files(pcb, Path(staged))

    return {
        "board": str(pcb),
        "staged": str(staged) if staged else None,
        "dry_run": dry_run,
        "vias_added": len(planned_vias),
        "vias": planned_vias,
    }


def _ensure_staged_project_files(board: Path, staged: Path) -> None:
    """Copy .kicad_pro / .kicad_dru next to a staged PCB so DRC picks up project design rules.

    Staged boards live under work_dir (often out/layout/), away from the live board's project
    files. Without siblings, kicad-cli DRC falls back to defaults and can report 0 shorts that
    appear after apply on the live board.
    """
    src_dir = Path(board).resolve().parent
    dst_dir = Path(staged).resolve().parent
    if src_dir == dst_dir:
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    for src in list(src_dir.glob("*.kicad_pro")) + list(src_dir.glob("*.kicad_dru")):
        dst = dst_dir / src.name
        if dst.resolve() == src.resolve():
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
        board = L.load(route_src)
        rules = cfg["rules_resolved"]
        res = float((cfg.get("grid") or {}).get("res", 0.05))
        eb = board.edge_box or (95.0, 95.0, 125.0, 155.0)
        layers = list(cfg.get("signal_layers") or ["F.Cu", "B.Cu"])
        routed: dict[str, list[dict]] = {}
        # Copper laid earlier in this call — later nets must clear it.
        fresh_tracks: list[dict] = []
        fresh_vias: list[dict] = []
        import numpy as np

        for net_name in target_nets:
            net_pads = [p for p in board.pads() if p.net == net_name]
            if len(net_pads) < 2:
                continue
            p0, p1 = net_pads[0], net_pads[1]
            nc = _class_for_net(rules, net_name)
            track_w = float(nc["width"])
            margin = float((cfg.get("handroute") or {}).get("search_margin", 8.0))
            bbox = (
                max(eb[0], min(p0.x, p1.x) - margin),
                max(eb[1], min(p0.y, p1.y) - margin),
                min(eb[2], max(p0.x, p1.x) + margin),
                min(eb[3], max(p0.y, p1.y) + margin),
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
            for op in board.pads():
                if op.net == net_name:
                    continue
                grow = _pair_clearance(rules, net_name, op.net) + track_w / 2
                for li, lay in enumerate(layers):
                    if lay in op.layers or "*.Cu" in op.layers:
                        _block_rect(
                            li,
                            op.x - op.w / 2 - grow,
                            op.y - op.h / 2 - grow,
                            op.x + op.w / 2 + grow,
                            op.y + op.h / 2 + grow,
                        )
                # Layer-change vias must not land in/on foreign pads (incl. EPADs).
                via_grow = _pair_clearance(rules, net_name, op.net) + via_d_net / 2
                _block_viaok(
                    op.box[0] - via_grow,
                    op.box[1] - via_grow,
                    op.box[2] + via_grow,
                    op.box[3] + via_grow,
                )
            for seg in board.segments:
                if seg.net == net_name:
                    continue
                if seg.layer not in layers:
                    continue
                li = layers.index(seg.layer)
                grow = seg.width / 2 + _pair_clearance(rules, net_name, seg.net) + track_w / 2
                _block_thick_seg(li, seg.x0, seg.y0, seg.x1, seg.y1, grow)
            for v in board.vias:
                if v.net == net_name:
                    continue
                grow = v.size / 2 + _pair_clearance(rules, net_name, v.net) + track_w / 2
                for li in layer_idxs:
                    _block_disk(li, v.x, v.y, grow)
            for seg in fresh_tracks:
                if seg["net"] == net_name:
                    continue
                if seg["layer"] not in layers:
                    continue
                li = layers.index(seg["layer"])
                grow = float(seg["width"]) / 2 + _pair_clearance(rules, net_name, seg["net"]) + track_w / 2
                _block_thick_seg(li, seg["x0"], seg["y0"], seg["x1"], seg["y1"], grow)

            s_li = 0 if (not p0.layers or layers[0] in p0.layers or "*.Cu" in p0.layers) else min(1, len(layers) - 1)
            g_li = 0 if (not p1.layers or layers[0] in p1.layers or "*.Cu" in p1.layers) else min(1, len(layers) - 1)
            six, siy = g.cell(p0.x, p0.y)
            gix, giy = g.cell(p1.x, p1.y)
            if not (0 <= six < g.nx and 0 <= siy < g.ny and 0 <= gix < g.nx and 0 <= giy < g.ny):
                continue
            # Own terminals must stay walkable even if clearance from nearby copper nibbled them.
            free[s_li][siy, six] = True
            free[g_li][giy, gix] = True
            start = (s_li, siy, six)
            goal = (g_li, giy, gix)
            hr = cfg.get("handroute") or {}
            via_cost = float(hr.get("via_cost", 1.6))
            pitch = grid_mod.via_pitch_cells(float(nc["via_h"]), float(rules.get("hole_hole", rules.get("hole_to_hole", 0.25))), res)
            cost_mult = {li: (1.0 if li == 0 else float((hr.get("inner") or {}).get("cost", 4.0))) for li in layer_idxs}
            path = grid_mod.astar(
                free,
                viaok,
                {start},
                {goal},
                layer_idxs,
                cost_mult,
                res,
                via_cost,
                pitch,
                max_expand=int(hr.get("max_expand", 2_500_000)),
            )
            if path:
                runs = grid_mod.simplify(path, lambda ix, iy: g.xy(ix, iy))
                seg_pairs, via_pts = grid_mod.runs_to_segments(runs)
                segs = []
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
                net_vias = []
                for pt in via_pts:
                    net_vias.append({
                        "net": net_name,
                        "x": round(pt[0], 4),
                        "y": round(pt[1], 4),
                        "size": float(nc["via_d"]),
                        "drill": float(nc["via_h"]),
                    })
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
            "segments_added": len(fresh_track_list),
            "vias_added": len(fresh_vias),
            "fanout_carried": len(carry_fanout),
            "routes": routed,
        }

    raise ValueError(f"unknown handroute action '{action}'")
