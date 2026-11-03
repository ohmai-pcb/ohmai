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

    existing_holes = [(v.x, v.y, v.drill / 2) for v in b.vias] + [
        (p.x, p.y, p.drill / 2) for p in b.pads() if p.hole
    ]
    all_pads = b.pads()
    eb = b.edge_box

    escape_specs = fo.get("escape") or []
    target_refs = {spec["ref"] for spec in escape_specs if isinstance(spec, dict) and "ref" in spec}

    planned_vias: list[dict] = []
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
        placed = None
        for r_step in steps:
            dist = max(p.w, p.h) / 2 + via_d / 2 + r_step
            for k in range(angles):
                ang = 2 * math.pi * k / angles
                vx = round(p.x + dist * math.cos(ang), 4)
                vy = round(p.y + dist * math.sin(ang), 4)
                if eb and (vx - via_d / 2 < eb[0] + edge_cl or vx + via_d / 2 > eb[2] - edge_cl or
                           vy - via_d / 2 < eb[1] + edge_cl or vy + via_d / 2 > eb[3] - edge_cl):
                    continue
                if any(math.hypot(vx - hx, vy - hy) < (via_h / 2 + hr + hole_hole) for hx, hy, hr in existing_holes):
                    continue
                v_box = (vx - via_d / 2, vy - via_d / 2, vx + via_d / 2, vy + via_d / 2)
                if any(o.net != p.net and L.box_gap(v_box, (o.x - o.w / 2, o.y - o.h / 2, o.x + o.w / 2, o.y + o.h / 2)) < pad_gap
                       for o in all_pads):
                    continue
                placed = {"ref": p.ref, "pad": p.num, "net": p.net, "x": vx, "y": vy,
                          "pad_x": p.x, "pad_y": p.y, "layer": p.layers[0] if p.layers else "F.Cu"}
                existing_holes.append((vx, vy, via_h / 2))
                break
            if placed:
                break
        if placed:
            planned_vias.append(placed)

    target = staged or (pcb if not dry_run else None)
    if target is not None:
        _append_copper_to_pcb(pcb, target, planned_vias, [], via_d, via_h, float(fo.get("track_w") or 0.25))

    return {
        "board": str(pcb),
        "staged": str(staged) if staged else None,
        "dry_run": dry_run,
        "vias_added": len(planned_vias),
        "vias": planned_vias,
    }


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
        if "pad_x" in v and "pad_y" in v:
            tree.append([
                "segment",
                ["start", str(v["pad_x"]), str(v["pad_y"])],
                ["end", str(v["x"]), str(v["y"])],
                ["width", str(default_w)],
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
        b = L.load(pcb) if pcb.is_file() else None
        routes_data = json.loads(routes_path.read_text(encoding="utf-8")) if routes_path.is_file() else {}
        return {
            "action": "status",
            "board": str(pcb),
            "base_exists": base_path.is_file(),
            "routes_exists": routes_path.is_file(),
            "segments_on_board": len(b.segments) if b else 0,
            "vias_on_board": len(b.vias) if b else 0,
            "hand_routed_nets": sorted((routes_data.get("routes") or {}).keys()) if isinstance(routes_data, dict) else [],
        }

    if action == "freeze":
        if not pcb.is_file():
            raise FileNotFoundError(f"{pcb}: board not found")
        b = L.load(pcb)
        snap = {
            "segments": [
                {"net": s.net, "layer": s.layer, "x0": s.x0, "y0": s.y0, "x1": s.x1, "y1": s.y1, "width": s.width}
                for s in b.segments
            ],
            "vias": [
                {"net": v.net, "x": v.x, "y": v.y, "size": v.size, "drill": v.drill}
                for v in b.vias
            ],
        }
        if not dry_run:
            base_path.parent.mkdir(parents=True, exist_ok=True)
            base_path.write_text(json.dumps(snap, indent=2) + "\n", encoding="utf-8")
        return {"action": "freeze", "base": str(base_path), "segments": len(snap["segments"]), "vias": len(snap["vias"])}

    if action in ("route", "apply"):
        if not pcb.is_file():
            raise FileNotFoundError(f"{pcb}: board not found")
        b = L.load(pcb)
        rules = cfg["rules_resolved"]
        res = float((cfg.get("grid") or {}).get("res", 0.05))
        eb = b.edge_box or (95.0, 95.0, 125.0, 155.0)
        layers = list(cfg.get("signal_layers") or ["F.Cu", "B.Cu"])
        routed: dict[str, list[dict]] = {}
        target_nets = nets or []
        for net_name in target_nets:
            net_pads = [p for p in b.pads() if p.net == net_name]
            if len(net_pads) < 2:
                continue
            p0, p1 = net_pads[0], net_pads[1]
            margin = float((cfg.get("handroute") or {}).get("search_margin", 8.0))
            bbox = (
                max(eb[0], min(p0.x, p1.x) - margin),
                max(eb[1], min(p0.y, p1.y) - margin),
                min(eb[2], max(p0.x, p1.x) + margin),
                min(eb[3], max(p0.y, p1.y) + margin),
            )
            g = grid_mod.Grid(bbox, res, len(layers))
            cl = float(rules["clearance"]) + float(rules["track_w"]) / 2
            for op in b.pads():
                if op.net != net_name:
                    for li, lay in enumerate(layers):
                        if lay in op.layers or "*.Cu" in op.layers:
                            g.block_rect(li, op.x - op.w / 2 - cl, op.y - op.h / 2 - cl, op.x + op.w / 2 + cl, op.y + op.h / 2 + cl)
            s_li = 0 if (not p0.layers or layers[0] in p0.layers or "*.Cu" in p0.layers) else min(1, len(layers) - 1)
            g_li = 0 if (not p1.layers or layers[0] in p1.layers or "*.Cu" in p1.layers) else min(1, len(layers) - 1)
            start = (s_li, *g.ij(p0.x, p0.y))
            goal = (g_li, *g.ij(p1.x, p1.y))
            path = grid_mod.astar(g, [start], {goal})
            if path:
                simp = grid_mod.simplify(path)
                segs = []
                for (l0, i0, j0), (l1, i1, j1) in zip(simp, simp[1:], strict=False):
                    if l0 == l1:
                        x0, y0 = g.xy(i0, j0)
                        x1, y1 = g.xy(i1, j1)
                        segs.append({
                            "net": net_name,
                            "layer": layers[l0],
                            "x0": round(x0, 4),
                            "y0": round(y0, 4),
                            "x1": round(x1, 4),
                            "y1": round(y1, 4),
                            "width": float(rules["track_w"]),
                        })
                routed[net_name] = segs

        all_tracks = [s for segs in routed.values() for s in segs]
        dst = staged or (pcb if not dry_run else None)
        if dst is not None:
            if all_tracks:
                _append_copper_to_pcb(pcb, dst, [], all_tracks, float(rules["via_d"]), float(rules["via_h"]), float(rules["track_w"]))
            elif staged and staged != pcb:
                shutil.copy2(pcb, staged)
        return {
            "action": action,
            "board": str(pcb),
            "staged": str(staged) if staged else None,
            "dry_run": dry_run,
            "routed_nets": sorted(routed.keys()),
            "segments_added": len(all_tracks),
            "routes": routed,
        }

    raise ValueError(f"unknown handroute action '{action}'")
