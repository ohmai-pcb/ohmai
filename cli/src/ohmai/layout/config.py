"""Project layout config: `hardware/layout.yaml` (or the file named by ohmai.yaml `layout.config`).

Everything project-specific for the layout steps lives in that file: plane nets and layers, inner signal layer,
escape / stitching / fanout settings, DSN edits for the autorouter, pour limits, hand-route rules and manual
geometry, escape-room rules. Board rules (net class clearances and widths, via size, hole and edge clearances)
come from the board's .kicad_pro; the file may override them. Unknown keys are errors (typos would silently do
nothing otherwise). Coordinates are KiCad board mm.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

OPEN = object()   # marker: a mapping whose keys are free (net names, refs, class names, manual entry names)

DEFAULTS: dict = {
    "board": None,                 # .kicad_pcb, default ohmai.yaml hardware.pcb
    "build": None,                 # argv that regenerates the unrouted board ("{python}" = KiCad Python)
    "work_dir": "out/layout",
    "base": "hardware/layout/routes-base.json",     # frozen autorouter copper (handroute freeze)
    "routes": "hardware/layout/handroute.json",     # rips + hand routes (handroute route / manual)
    "grid": {"res": 0.05},
    "copper": None,                # copper layer names, default all of the board's
    "signal_layers": ["F.Cu", "B.Cu"],
    "inner_signal_layer": None,    # an inner (pour) layer that may carry signals
    "planes": OPEN,                # net -> plane layer
    "via": {"d": None, "h": None},
    "classes": OPEN,               # {class: {clearance, width}} over the .kicad_pro classes
    "rules": {"margin": 0.03, "via_hole_cu": None, "pth_hole_trk": None, "hole_hole": None, "edge": None},
    "fanout": {"clearance": None, "hole_trk": None, "hole_hole": None, "edge": None, "pad_gap": 0.12,
               "track_w": 0.25, "bridge_reach": 2.6, "steps": [0.30, 0.45, 0.65, 0.9, 1.2], "angles": 24,
               "channel": None, "escape": [], "stitch": None},
    "autoroute": {"lock": "toolchain.lock.yaml", "passes": 30, "timeout": "00:10:00", "via_costs": 50,
                  "opt_passes": 2, "threads": None, "netns": True, "inner_nets": None,
                  "dsn": {"drop_pins": [], "keepouts": [], "grow_cutouts": 0.25, "plane_keepout_margin": 0.025,
                          "plane_via_keepout": 0.5, "class_split": None, "widen_padstacks": [],
                          "inner_class": {"name": "InnerOk", "width": 0.15, "clearance": 0.1}}},
    "pour": None,                  # {net, layer, zone_clearance, edge, attach, min_neck, local_neck, local}
    "handroute": {"via_cost": 1.6, "plane_window": 4.0, "search_margin": 8.0, "neck_w": 0.1,
                  "neck_classes": ["Default"], "alt_widths": OPEN, "no_inner": [], "no_rip": [], "rip_cap": 4,
                  "rip_pen": 0.4, "inner": {"cost": 4.0, "max_run": 2.5, "wide_w": 1.4, "narrow_pen": 0.3},
                  "max_expand": 2500000, "solid_pads": OPEN, "manual": OPEN},
    "escape_room": None,
    "placement": None,
    "critical_loops": [],
    "keepouts": [],
    "layout_checklist": [],
}
POUR_DEFAULTS = {"net": None, "box": None, "layer": None, "zone_clearance": 0.2, "edge": 0.2, "attach": 0.25, "min_neck": 0.5,
                 "local_neck": 0.8, "local": {"refs": [], "margin": 2.0, "decoupler_prefix": "C", "decoupler_dist": 2.0}}


class ConfigError(ValueError):
    pass


def _merge(default, given, where: str):
    if default is OPEN:
        if given is not None and not isinstance(given, dict):
            raise ConfigError(f"{where}: expected a mapping")
        return dict(given or {})
    if isinstance(default, dict) and default:
        if given is None:
            return {k: _merge(v, None, f"{where}.{k}") for k, v in default.items()}
        if not isinstance(given, dict):
            raise ConfigError(f"{where}: expected a mapping")
        unknown = sorted(set(given) - set(default))
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)} (known: {', '.join(default)})")
        return {k: _merge(v, given.get(k), f"{where}.{k}") for k, v in default.items()}
    return copy.deepcopy(default) if given is None else given


def config_path(project) -> Path:
    lay = (project.config or {}).get("layout") or {}
    return project.root / (lay.get("config") if isinstance(lay, dict) and lay.get("config") else "hardware/layout.yaml")


def load(project, path: Path | None = None) -> dict:
    """Merged config with absolute paths and the board rules resolved (`rules_resolved`)."""
    path = path or config_path(project)
    given = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    cfg = _merge(DEFAULTS, given or {}, "layout")
    if cfg["pour"] is not None:
        cfg["pour"] = _merge({**POUR_DEFAULTS, "local": POUR_DEFAULTS["local"]}, cfg["pour"], "layout.pour")
    cfg["_file"] = str(path) if path.is_file() else None
    root = project.root
    cfg["root"] = str(root)
    cfg["board"] = str(root / cfg["board"]) if cfg["board"] else str(project.pcb)
    for k in ("work_dir", "base", "routes"):
        cfg[k] = str(root / cfg[k])
    cfg["project_file"] = str(Path(cfg["board"]).with_suffix(".kicad_pro"))
    cfg["rules_resolved"] = resolve_rules(cfg)
    return cfg


def resolve_rules(cfg: dict) -> dict:
    """Board rules from the .kicad_pro (net classes, patterns, via, hole and edge minimums) with config overrides."""
    pro = Path(cfg["project_file"])
    data = json.loads(pro.read_text(encoding="utf-8")) if pro.is_file() else {}
    ns = data.get("net_settings", {})
    classes = {c["name"]: {"clearance": c.get("clearance", 0.1), "width": c.get("track_width", 0.15),
                           "via_d": c.get("via_diameter"), "via_h": c.get("via_drill")} for c in ns.get("classes", [])}
    classes.setdefault("Default", {"clearance": 0.1, "width": 0.15, "via_d": 0.45, "via_h": 0.2})
    for name, over in (cfg.get("classes") or {}).items():
        classes.setdefault(name, dict(classes["Default"])).update(over or {})
    r = (data.get("board", {}).get("design_settings", {}).get("rules", {}))
    rules = cfg["rules"]
    via_hole_cu = rules["via_hole_cu"] if rules["via_hole_cu"] is not None else r.get("min_hole_clearance", 0.25)
    out = {
        "classes": classes,
        "patterns": [(p["pattern"], p["netclass"]) for p in ns.get("netclass_patterns", []) or []],
        "via_d": cfg["via"]["d"] or classes["Default"].get("via_d") or 0.45,
        "via_h": cfg["via"]["h"] or classes["Default"].get("via_h") or 0.2,
        "margin": rules["margin"],
        "via_hole_cu": via_hole_cu,
        "pth_hole_trk": rules["pth_hole_trk"] if rules["pth_hole_trk"] is not None else via_hole_cu,
        "hole_hole": rules["hole_hole"] if rules["hole_hole"] is not None else r.get("min_hole_to_hole", 0.25),
        "edge": rules["edge"] if rules["edge"] is not None else r.get("min_copper_edge_clearance", 0.2),
    }
    fo = cfg["fanout"]
    maxcl = max(c["clearance"] for c in classes.values())
    out["fanout"] = {
        "clearance": fo["clearance"] if fo["clearance"] is not None else round(maxcl + 0.02, 4),
        "hole_trk": fo["hole_trk"] if fo["hole_trk"] is not None else round(out["pth_hole_trk"] + 0.02, 4),
        "hole_hole": fo["hole_hole"] if fo["hole_hole"] is not None else round(out["hole_hole"] + 0.05, 4),
        "edge": fo["edge"] if fo["edge"] is not None else round(out["edge"] + 0.05, 4),
        "pad_gap": fo["pad_gap"], "track_w": fo["track_w"],
    }
    out["clearance"] = classes["Default"]["clearance"]
    out["track_w"] = classes["Default"]["width"]
    out["hole_to_hole"] = out["hole_hole"]
    out["plane_nets"] = sorted(cfg.get("planes") or {})
    return out
