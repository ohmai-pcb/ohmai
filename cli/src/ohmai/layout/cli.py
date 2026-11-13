# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`ohmai layout ...` subcommands."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _project():
    from ohmai.project import NotAProject, find_project

    try:
        return find_project()
    except NotAProject as e:
        print(f"ohmai: {e}", file=sys.stderr)
        sys.exit(2)


def _cfg(project, a):
    from ohmai.layout import config as lcfg

    try:
        return lcfg.load(project, project.root / a.config if getattr(a, "config", None) else None)
    except lcfg.ConfigError as e:
        print(f"ohmai layout: {e}", file=sys.stderr)
        sys.exit(2)


def _xy(s: str) -> tuple[float, ...]:
    return tuple(float(v) for v in s.split(","))


def _resolve_staged(p, cfg: dict, staged_arg: str | None) -> Path | None:
    if not staged_arg:
        return None
    if staged_arg == "auto":
        return Path(cfg["work_dir"]) / "staged.kicad_pcb"
    sp = Path(staged_arg)
    return sp if sp.is_absolute() else (p.root / sp)


def cmd_fit(a) -> int:
    """Does REF fit at x,y[,rot]? With --scan: the nearest free spots around it."""
    from ohmai.layout import placement

    p = _project()
    cfg = _cfg(p, a)
    pcb = Path(cfg["board"])
    board = placement.load(pcb)
    if a.ref not in board.footprints:
        print(f"ohmai layout fit: no footprint {a.ref} on {pcb.name}", file=sys.stderr)
        return 2
    fp = board.footprints[a.ref]
    v = _xy(a.at) if a.at else (fp.x, fp.y)
    rot = v[2] if len(v) > 2 else None
    ignore = tuple(a.ignore.split(",")) if a.ignore else ()
    igc = tuple(a.ignore_courtyard.split(",")) if a.ignore_courtyard else ()
    out: dict = {"ref": a.ref, "at": [v[0], v[1], fp.rot if rot is None else rot]}
    ok, why = placement.fits(board, a.ref, v[0], v[1], rot, a.edge_margin, ignore, igc, a.pad_gap)
    out.update(fits=ok, blockers=why)
    if a.scan:
        rots = tuple(float(r) for r in a.rots.split(",")) if a.rots else None
        out["free"] = [{"dist": d, "x": x, "y": y, "rot": r} for d, x, y, r in
                       placement.scan(board, a.ref, (v[0], v[1]), a.radius, a.step, rots, a.edge_margin, a.limit, ignore, igc, a.pad_gap)]
    if a.rules and cfg["escape_room"]:
        from ohmai.layout import escape

        moved = placement.load(pcb)
        placement.move(moved, a.ref, v[0], v[1], rot)
        out["escape_room"] = [list(f) for f in escape.Rules(moved, {**escape.DEFAULTS, **cfg["escape_room"]},
                                                              cfg["rules_resolved"]).run() if f[1] == "FAIL"]
    staged_path = _resolve_staged(p, cfg, getattr(a, "staged", None))
    if staged_path is not None:
        placement.stage_move(pcb, staged_path, a.ref, v[0], v[1], rot)
        out["staged"] = str(staged_path)
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"{a.ref} at {out['at'][0]:.3f},{out['at'][1]:.3f} rot {out['at'][2]:g}: "
              + ("fits" if ok else "does not fit"))
        for w in why:
            print(f"  - {w}")
        for f in out.get("free", []):
            print(f"  free: {f['x']:.3f},{f['y']:.3f} rot {f['rot']:g} ({f['dist']:.2f} mm away)")
        if a.scan and not out["free"]:
            print(f"  no free spot within {a.radius} mm")
        for f in out.get("escape_room", []):
            print(f"  escape-room after the move: FAIL [{f[0]}] {f[2]}")
        if staged_path is not None:
            print(f"  staged board written to {staged_path}")
    return 0 if ok and not out.get("escape_room") else 1


def cmd_fanout(a) -> int:
    from ohmai.layout import steps

    p = _project()
    cfg = _cfg(p, a)
    staged_path = _resolve_staged(p, cfg, getattr(a, "staged", None))
    try:
        res = steps.run_fanout(cfg, staged=staged_path, dry_run=a.dry_run,
                               nets=a.nets, exclude_near=a.exclude_near, exclude_pads=a.exclude_pad)
    except Exception as e:
        print(f"ohmai layout fanout: {e}", file=sys.stderr)
        return 1
    skipped = res.get("skipped") or []
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        mode = " [dry-run]" if a.dry_run else (f" [staged -> {staged_path}]" if staged_path else "")
        print(f"fanout{mode}: {res['vias_added']} via(s) planned")
        if skipped:
            print(f"fanout: skipped {len(skipped)} pad(s) with no legal escape (no illegal copper written)",
                  file=sys.stderr)
    # Prefer zero fanout copper over illegal copper: non-zero when pads were skipped.
    return 1 if skipped else 0


def cmd_autoroute(a) -> int:
    from ohmai.layout import router_log, steps

    p = _project()
    cfg, prepared, run = {}, {}, {}
    error = None
    code = 1
    try:
        cfg = _cfg(p, a)
        dsn_in = (p.root / a.dsn) if a.dsn and not Path(a.dsn).is_absolute() else (Path(a.dsn) if a.dsn else None)
        prepared = steps.prepare_autoroute(cfg, dsn_in=dsn_in, passes=a.passes,
            opt_passes=a.opt_passes, timeout=a.timeout, via_costs=a.via_costs,
            threads=a.threads, nets=a.nets, exclude=a.exclude_nets)
        if a.prepare_only:
            run = {"ok": False, "fail_reasons": ["prepare_only_not_routed"]}
            code = 0
        else:
            run = steps.run_autoroute(prepared)
            code = 0 if run.get("ok") else 1
    except (Exception, KeyboardInterrupt) as e:
        error = str(e) or type(e).__name__
        code = 130 if isinstance(e, KeyboardInterrupt) else 1
        for stream in ("stdout", "stderr"):
            text = getattr(e, stream, None)
            if text:
                if isinstance(text, bytes):
                    text = text.decode(errors="replace")
                run[stream + "_tail"] = "\n".join(text.splitlines()[-100:])
        print(f"ohmai layout autoroute: {e}", file=sys.stderr)
    try:
        record = router_log.append_run(p.root, cfg, prepared, run, vars(a), error, a.prepare_only)
    except Exception as e:
        print(f"ohmai layout autoroute: evidence logging failed: {e}", file=sys.stderr)
        return 1
    out = {**prepared, **run, "run_log_id": record["id"], "drc_status": record["drc_status"]}
    if a.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"autoroute: ok={run.get('ok')}, route_wires={run.get('ses_route_wires')}, "
              f"route_vias={run.get('ses_route_vias')}, log={record['id']}, DRC={record['drc_status']}")
        for w in [x for x in prepared.get("dsn_log") or [] if str(x).startswith("WARNING")] + list(
                run.get("warnings") or []):
            print(f"autoroute: {w}", file=sys.stderr)
        if run.get("fail_reasons"):
            print(f"autoroute: FAIL ({', '.join(run['fail_reasons'])})", file=sys.stderr)
        if run.get("stderr_tail"):
            print(run["stderr_tail"], file=sys.stderr)
    if record["measurement_errors"] or record["worse_nets"]:
        return 1
    if record["drc_delta"] and any(record["drc_delta"][k] > 0 for k in
                                    ("nonzone_clearance", "safety", "connection_width", "shorts", "crossings")):
        return 1
    return code


def cmd_pour(a) -> int:
    from ohmai.layout import steps

    p = _project()
    cfg = _cfg(p, a)
    staged_path = _resolve_staged(p, cfg, getattr(a, "staged", None))
    board = getattr(a, "board", None)
    board_path = (Path(board) if Path(board).is_absolute() else Path.cwd() / board) if board else None
    try:
        sections = [_parse_section(t) for t in (getattr(a, "section", None) or [])]
        widths_at = [_parse_xy(t) for t in (getattr(a, "width_at", None) or [])]
    except ValueError as e:
        print(f"ohmai layout pour: {e}", file=sys.stderr)
        return 2
    try:
        res = steps.run_pour(cfg, net=a.net, layer=a.layer, staged=staged_path, board_path=board_path,
                             sections=sections, widths_at=widths_at)
    except Exception as e:
        print(f"ohmai layout pour: {e}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        xy_str = f" at ({res['neck_xy'][0]:.2f}, {res['neck_xy'][1]:.2f})" if res.get("neck_xy") else ""
        st = "PASS" if res["ok"] else "FAIL"
        print(f"{st} pour {res['net']} ({res['layer']}): {res['pieces']} piece(s), "
              f"neck {res['neck']:.2f} mm (min {res['min_neck']:.2f} mm){xy_str}, area {res['area']:.1f} mm^2")
        if res.get("pieces", 0) > 1:
            print("  per-piece necks (largest first): " + ", ".join(
                f"{d['area']:.0f} mm^2 -> {'-' if d['neck'] is None else format(d['neck'], '.2f')}"
                for d in res.get("pieces_detail", [])))
        if res.get("unattached"):
            print(f"  {len(res['unattached'])} same-net via/pad(s) do not touch this pour (not part of the neck): "
                  + ", ".join(u["name"] for u in res["unattached"][:8]))
        for row in res.get("sections", []):
            print(f"  section {row['axis']}={row['value']}: {row['start']} -> {row['end']}, width {row['width']:.2f} mm "
                  f"(chord {row['chord']:.2f} mm)")
        for row in res.get("widths_at", []):
            w = "not in pour" if row["width"] is None else f"{row['width']:.2f} mm"
            print(f"  width at ({row['xy'][0]}, {row['xy'][1]}): {w}")
    return 0 if res["ok"] else 1


def _parse_section(text: str) -> tuple[str, float]:
    axis, sep, value = text.partition("=")
    if axis not in ("x", "y") or not sep:
        raise ValueError(f"--section {text!r}: use x=MM or y=MM")
    return axis, float(value)


def _parse_xy(text: str) -> tuple[float, float]:
    parts = text.split(",")
    if len(parts) != 2:
        raise ValueError(f"--width-at {text!r}: use X,Y in mm")
    return float(parts[0]), float(parts[1])


def cmd_handroute(a) -> int:
    from ohmai.layout import steps

    p = _project()
    cfg = _cfg(p, a)
    staged_path = _resolve_staged(p, cfg, getattr(a, "staged", None))
    cfg["handroute"] = dict(cfg.get("handroute") or {})
    for key in ("budget_seconds", "max_total_expand", "max_searches"):
        value = getattr(a, key, None)
        if value is not None:
            cfg["handroute"][key] = value
    nets = [n.strip() for n in a.nets.split(",") if n.strip()] if a.nets else None
    try:
        res = steps.run_handroute(cfg, action=a.action, nets=nets, staged=staged_path,
                                  dry_run=a.dry_run, verify_drc=(a.action in {"route", "apply"}))
    except Exception as e:
        print(f"ohmai layout handroute: {e}", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        if a.action == "status":
            print(f"handroute status: {res['segments_on_board']} segment(s), {res['vias_on_board']} via(s)")
        elif a.action == "freeze":
            print(f"handroute freeze: saved {res['segments']} segment(s), {res['vias']} via(s) to {res['base']}")
        else:
            print(f"handroute {a.action}: routed {len(res['routed_nets'])} net(s), {res['segments_added']} segment(s)")
            if res.get("budget_exhausted"):
                print(f"handroute stopped: {res['budget_exhausted']} (partial, not complete)", file=sys.stderr)
            if res.get("proposal_rejected"):
                print("handroute rejected: native DRC found narrow connections, illegal copper or worse nets; no copper written",
                      file=sys.stderr)
    return 1 if res.get("budget_exhausted") or res.get("proposal_rejected") else 0


def cmd_apply_changes(a) -> int:
    from ohmai.layout import changes

    project = _project()
    result = changes.apply(_cfg(project, a), Path(a.change_list), dry_run=a.dry_run)
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 1


def cmd_zones(a) -> int:
    from ohmai.layout import zones

    board = Path(a.board) if a.board else Path(_cfg(_project(), a)["board"])
    rows = zones.list_zones(board, a.net)
    if a.json:
        print(json.dumps(rows, indent=2))
        return 0
    for r in rows:
        tag = "" if r["editable"] else "  (" + r["reason"] + ")"
        print(f"{r['uuid']}  {r['net'] or '<no net>'}  {','.join(r['layers'])}  prio {r['priority']}  "
              f"{r['points']} pts{'  name ' + r['name'] if r['name'] else ''}{tag}")
    return 0


def cmd_current(a) -> int:
    from ohmai.layout import current

    try:
        layers = [x.strip() for x in a.layers.split(",") if x.strip()]
        board = Path(a.board) if a.board else None
        if board is None and not a.copper_all:
            try:
                from ohmai.project import NotAProject, find_project
                board = Path(_cfg(find_project(), a)["board"])
            except NotAProject:
                board = None
        copper = current.default_copper(layers)
        source = "default 1 oz outer / 0.5 oz inner"
        if board is not None and board.is_file():
            stack = current.board_copper(board)
            if stack:
                copper.update({n: t for n, t in stack.items() if n in layers})
                source = f"stackup of {board.name}"
        if a.copper_all:
            copper = {n: current.parse_thickness(a.copper_all) for n in layers}
            source = "--copper-all"
        copper.update(current.parse_layer_values(a.copper, current.parse_thickness))
        widths = current.parse_layer_values(a.width, float)
        widths.update(current.parse_layer_values(a.pour_json, current.pour_neck))
        targets = [current.parse_target(t, a.rise) for t in (a.target or [])]
        if not targets:
            raise ValueError("give at least one --target NAME=AMPS[@RISE_C]")
        via = None
        if a.via_drill:
            via = dict(drill=a.via_drill, plating=current.parse_thickness(a.via_plating), count=a.via_count)
        res = current.report(targets, layers, copper, a.method, widths, a.length, via, a.ambient)
        res["copper_source"] = source
    except (ValueError, OSError) as e:
        print(f"ohmai layout current: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2) if a.json else f"copper: {source}\n" + current.format_text(res))
    return 0 if res["ok"] is not False else 1


def add_parser(sub) -> None:
    s = sub.add_parser("layout", help="layout steps (phase 8): placement fit / free-area scan; fanout, autoroute, "
                                      "pour, handroute (KiCad Python)",
                       description="Project settings: hardware/layout.yaml (or ohmai.yaml layout.config), see "
                                   "docs/LAYOUT.md.")
    s.add_argument("--config", help="layout config file (relative to the project root)")
    ls = s.add_subparsers(dest="layout_cmd", required=True)
    f = ls.add_parser("fit", help="would a part fit at a position? (courtyards on its side, board edge); --scan "
                                  "lists the nearest free spots")
    f.add_argument("ref")
    f.add_argument("--at", help="x,y[,rot] in board mm (default: where it is)")
    f.add_argument("--scan", action="store_true")
    f.add_argument("--radius", type=float, default=2.0)
    f.add_argument("--step", type=float, default=0.1)
    f.add_argument("--rots", help="rotations to try with --scan, e.g. 0,90 (default: its own)")
    f.add_argument("--limit", type=int, default=5)
    f.add_argument("--edge-margin", type=float, default=0.0, help="courtyard to board edge, mm")
    f.add_argument("--ignore", help="refs that do not count at all, courtyard or pads (the parts you move next), "
                                    "comma list")
    f.add_argument("--ignore-courtyard", help="refs whose courtyards do not count but whose pads still do "
                                              "(e.g. castellated edge pins), comma list")
    f.add_argument("--pad-gap", type=float, default=0.1, help="pad copper to other-net pad copper, mm (default 0.1)")
    f.add_argument("--rules", action="store_true", help="also run the escape-room rules with the part moved")
    f.add_argument("--staged", nargs="?", const="auto", help="write candidate board move to a staged .kicad_pcb copy")
    f.add_argument("--json", action="store_true")
    f.set_defaults(fn=cmd_fit)

    fo = ls.add_parser("fanout", help="escape vias on plane/signal pads and GND stitching grid")
    fo.add_argument("--staged", nargs="?", const="auto", help="write fanout copper to a staged .kicad_pcb copy")
    fo.add_argument("--dry-run", action="store_true", help="compute fanout vias without modifying the board")
    fo.add_argument("--nets", help="regex of nets to fan out (overrides escape ref selection)")
    fo.add_argument("--exclude-near", action="append", help="exclude via AND stub within REF.PAD:radius_mm; repeatable")
    fo.add_argument("--exclude-pad", action="append", help="skip source REF.PAD; repeatable")
    fo.add_argument("--json", action="store_true")
    fo.set_defaults(fn=cmd_fanout)

    ar = ls.add_parser("autoroute", help="prepare DSN and run/configure Freerouting with capped opt-passes and timeout")
    ar.add_argument("--dsn", help="input .dsn file to transform using layout.yaml autoroute.dsn")
    ar.add_argument("--passes", type=int, default=None, help="max autorouter passes (default from layout.yaml: 30)")
    ar.add_argument("--opt-passes", type=int, default=2, help="max optimizer passes (default from layout.yaml: 2)")
    ar.add_argument("--timeout", default="00:10:00", help="per-run timeout HH:MM:SS (default from layout.yaml: 00:10:00)")
    ar.add_argument("--via-costs", type=int, default=None, help="via cost penalty (default: 50)")
    ar.add_argument("--threads", type=int, default=None, help="router thread count")
    ar.add_argument("--nets", default=None,
                    help="regex of nets to keep in the DSN network (scoped Freeroute; others dropped)")
    ar.add_argument("--exclude-nets", default=None, help="regex of nets to drop from the DSN network")
    ar.add_argument("--prepare-only", action="store_true", help="only prepare DSN and print router config")
    ar.add_argument("--json", action="store_true")
    ar.add_argument("--drc-before", help="full JSON DRC before the staged import (measurement evidence)")
    ar.add_argument("--drc-after", help="full JSON DRC after staged import and refill; regressions return nonzero")
    ar.set_defaults(fn=cmd_autoroute)

    po = ls.add_parser("pour", help="verify plane continuity (pieces == 1) and narrowest neck width + (x, y) location")
    po.add_argument("--net", help="plane net name (overrides layout.yaml pour.net)")
    po.add_argument("--layer", help="plane layer name (overrides layout.yaml pour.layer)")
    po_src = po.add_mutually_exclusive_group()
    po_src.add_argument("--staged", nargs="?", const="auto", help="evaluate pour on a staged .kicad_pcb copy")
    po_src.add_argument("--board", help="evaluate pour on this .kicad_pcb (read-only; refilled on a scratch copy)")
    po.add_argument("--section", action="append", metavar="x=MM|y=MM",
                    help="report copper width across the line x=MM or y=MM (repeatable)")
    po.add_argument("--width-at", action="append", metavar="X,Y",
                    help="report the local perpendicular width through point X,Y (repeatable)")
    po.add_argument("--json", action="store_true")
    po.set_defaults(fn=cmd_pour)

    ac = ls.add_parser("apply-changes", help="validate/apply a versioned layout transaction with native refill/DRC rollback")
    ac.add_argument("change_list")
    ac.add_argument("--dry-run", action="store_true", help="run full validation and DRC without changing the board")
    ac.set_defaults(fn=cmd_apply_changes)

    zo = ls.add_parser("zones", help="list copper zones with UUID and the expect block for zone.edit/zone.delete")
    zo.add_argument("--net", help="net name regex (full match)")
    zo.add_argument("--board", help=".kicad_pcb (default: project board)")
    zo.add_argument("--json", action="store_true")
    zo.set_defaults(fn=cmd_zones)

    cu = ls.add_parser("current", help="IPC-2152 style current/width sizing for tracks, pours/corridors and vias",
                       description="Screening calculation; see docs/LAYOUT.md 'Current sizing'.")
    cu.add_argument("--target", action="append", metavar="NAME=AMPS[@RISE_C]",
                    help="current to check, repeatable, e.g. continuous=15 combined=19.5 peak=20@30")
    cu.add_argument("--rise", type=float, default=20.0, help="allowed temperature rise, degC (default 20)")
    cu.add_argument("--layers", default="F.Cu,B.Cu", help="copper layers that share the current (default F.Cu,B.Cu)")
    cu.add_argument("--copper", action="append", metavar="LAYER=THICK", help="copper per layer: 1oz, 2oz, 35um, 0.07mm")
    cu.add_argument("--copper-all", metavar="THICK", help="same copper weight on every listed layer (e.g. 2oz)")
    cu.add_argument("--board", help=".kicad_pcb whose stackup gives copper thickness (default: project board)")
    cu.add_argument("--width", action="append", metavar="LAYER=MM", help="measured narrowest width on a layer")
    cu.add_argument("--pour-json", action="append", metavar="LAYER=FILE",
                    help="take the width from an `ohmai layout pour --json` neck")
    cu.add_argument("--length", type=float, help="path length, mm (adds resistance, drop and dissipation)")
    cu.add_argument("--ambient", type=float, default=25.0, help="ambient for resistance, degC (default 25)")
    cu.add_argument("--via-drill", type=float, help="stitching via drill, mm (adds per-via current and count)")
    cu.add_argument("--via-plating", default="20um", help="via barrel plating (default 20um; ask your fab)")
    cu.add_argument("--via-count", type=int, help="vias per layer change to check")
    cu.add_argument("--method", choices=["ipc2152", "ipc2221"], default="ipc2152")
    cu.add_argument("--json", action="store_true")
    cu.set_defaults(fn=cmd_current)

    hr = ls.add_parser("handroute", help="grid A* for tail nets (thin-first by netclass width; prefer pours then retry missed nets), freeze, staged apply")
    hr.add_argument("action", nargs="?", default="status", choices=["status", "route", "freeze", "apply"])
    hr.add_argument("--nets", help="comma-separated net names to route")
    hr.add_argument("--staged", nargs="?", const="auto", help="write routed tracks to a staged .kicad_pcb copy")
    hr.add_argument("--budget-seconds", type=float, help="total routing time budget (default 60s)")
    hr.add_argument("--max-total-expand", type=int, help="total A* expansions across nets/widths")
    hr.add_argument("--max-searches", type=int, help="total A* calls across nets/widths")
    hr.add_argument("--dry-run", action="store_true")
    hr.add_argument("--json", action="store_true")
    hr.set_defaults(fn=cmd_handroute)
