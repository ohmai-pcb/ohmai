"""Project-level 3D STEP model fetcher, missing-model detector, and dummy STEP generator.

Fetches only the official KiCad `.step` models referenced by a project's PCB into
`<project>/out/3dmodels/` (gitignored via `out/`) so shared storage (`$OHMAI_KICAD_LIBS`)
does not bloat with the multi-GB `kicad-packages3D` repository. Footprints whose `.step`
model is missing (either no `(model ...)` entry on a BOM component footprint or HTTP 404
upstream in `kicad-packages3D`) are flagged and optionally populated with a parametric
ISO-10303-21 STEP AP214 bounding-box solid derived from the footprint's `F.Fab` / `F.CrtYd`
outline and component height constraint.
"""
from __future__ import annotations

import csv
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from .project import Project
from .sexp import find, first, parse

PACKAGES3D_RAW_URL = (
    "https://gitlab.com/kicad/libraries/kicad-packages3D/-/raw/{tag}/{rel_path}"
)
DUMMY_STEP_MARKER = "OHMAI_DUMMY_STEP_BLOCK"


def project_models_dir(project: Project) -> Path:
    return project.out / "3dmodels"


def is_non_component_footprint(fp_id: str, ref: str) -> bool:
    lib, _, name = fp_id.partition(":")
    if lib in {"NetTie", "Jumper", "TestPoint", "MountingHole", "Fiducial"}:
        return True
    lower = name.lower()
    if any(
        tok in lower
        for tok in (
            "testpoint",
            "solderjumper",
            "nettie",
            "edge_castellated",
            "mountinghole",
            "fiducial",
        )
    ):
        return True
    if re.match(r"^(TP|JP|NT|MH|FID)\d+$", ref):
        return True
    return False


def _extract_layer_bbox(fp_node: list, layer_name: str) -> tuple[float, float, float, float] | None:
    pts: list[tuple[float, float]] = []
    for tag in ("fp_line", "fp_rect"):
        for item in find(fp_node, tag):
            lyr = first(item, "layer")
            if not lyr or len(lyr) < 2 or lyr[1] != layer_name:
                continue
            s = first(item, "start")
            e = first(item, "end")
            if s and e and len(s) >= 3 and len(e) >= 3:
                pts.append((float(s[1]), float(s[2])))
                pts.append((float(e[1]), float(e[2])))
    for poly in find(fp_node, "fp_poly"):
        lyr = first(poly, "layer")
        if not lyr or len(lyr) < 2 or lyr[1] != layer_name:
            continue
        pts_node = first(poly, "pts")
        if pts_node:
            for xy in find(pts_node, "xy"):
                if len(xy) >= 3:
                    pts.append((float(xy[1]), float(xy[2])))
    for circ in find(fp_node, "fp_circle"):
        lyr = first(circ, "layer")
        if not lyr or len(lyr) < 2 or lyr[1] != layer_name:
            continue
        c = first(circ, "center")
        e = first(circ, "end")
        if c and e and len(c) >= 3 and len(e) >= 3:
            cx, cy = float(c[1]), float(c[2])
            ex, ey = float(e[1]), float(e[2])
            r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
            pts.extend([(cx - r, cy - r), (cx + r, cy + r)])
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if max(xs) - min(xs) < 1e-3 or max(ys) - min(ys) < 1e-3:
        return None
    return (min(xs), max(xs), min(ys), max(ys))


def footprint_bbox_xy(fp_node: list) -> tuple[float, float, float, float, str]:
    """Return (xmin, xmax, ymin, ymax, source_layer) in KiCad 2D footprint coordinates."""
    for lyr in ("F.Fab", "B.Fab", "F.CrtYd", "B.CrtYd"):
        bbox = _extract_layer_bbox(fp_node, lyr)
        if bbox is not None:
            return (*bbox, lyr)
    pts: list[tuple[float, float]] = []
    for pad in find(fp_node, "pad"):
        at = first(pad, "at")
        sz = first(pad, "size")
        if at and sz and len(at) >= 3 and len(sz) >= 3:
            px, py = float(at[1]), float(at[2])
            sx, sy = float(sz[1]) / 2.0, float(sz[2]) / 2.0
            pts.extend([(px - sx, py - sy), (px + sx, py + sy)])
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), max(xs), min(ys), max(ys), "pads")
    return (-0.5, 0.5, -0.5, 0.5, "default")


def _load_bom_and_constraints(project: Project) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, Any]]]:
    ref_to_bom: dict[str, dict[str, str]] = {}
    if project.bom.exists():
        with project.bom.open() as f:
            for row in csv.DictReader(f):
                refs_raw = row.get("ref", "")
                for r in re.split(r"[\s,]+", refs_raw.strip()):
                    if r:
                        ref_to_bom[r] = row
    constraints: dict[str, dict[str, Any]] = {}
    cdir = project.root / "parts" / "constraints"
    if cdir.is_dir():
        for p in sorted(cdir.glob("*.yaml")):
            try:
                data = yaml.safe_load(p.read_text()) or {}
                if isinstance(data, dict):
                    constraints[p.stem] = data
            except Exception:
                pass
    return ref_to_bom, constraints


def infer_component_height_mm(
    project: Project,
    ref: str,
    fp_id: str,
    value: str,
    ref_to_bom: dict[str, dict[str, str]] | None = None,
    constraints: dict[str, dict[str, Any]] | None = None,
) -> tuple[float, str]:
    """Infer component Z height (mm) and citation string from constraints, BOM, or package type."""
    if ref_to_bom is None or constraints is None:
        ref_to_bom, constraints = _load_bom_and_constraints(project)

    bom_row = ref_to_bom.get(ref, {})
    mpn = bom_row.get("mpn", "") or value

    # 1. Check parts/constraints/<mpn>.yaml package string for L x W x H
    for key, cdata in constraints.items():
        if key and (key in mpn or mpn in key):
            pkg_str = str(cdata.get("package", ""))
            m = re.search(
                r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*mm",
                pkg_str,
                re.IGNORECASE,
            )
            if m:
                return float(m.group(3)), f"parts/constraints/{key}.yaml ({pkg_str})"

    # 2. Check BOM notes / package columns for explicit height or LxWxH
    for col in ("package", "notes"):
        txt = str(bom_row.get(col, ""))
        m = re.search(
            r"(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)",
            txt,
            re.IGNORECASE,
        )
        if m:
            return float(m.group(3)), f"bom/bom.csv:{col}"
        m_h = re.search(r"(\d+(?:\.\d+)?)\s*mm\s*(?:high|height|tall|max\s+height)", txt, re.IGNORECASE)
        if m_h:
            return float(m_h.group(1)), f"bom/bom.csv:{col}"

    # 3. Package-specific rules
    fp_lower = fp_id.lower()
    if "type-c-16pin-2md-073" in fp_lower or "2md(073)" in mpn.lower():
        return 3.16, "SHOU HAN TYPE-C 16PIN 2MD(073) datasheet (3.16 mm shell height)"
    if "usb_c" in fp_lower:
        return 3.20, "USB-C SMD receptacle standard height (3.20 mm)"
    if "pcc-smd" in fp_lower:
        return 2.37, "Omega PCC-SMD datasheet (2.37 mm height above PCB)"
    if "ts-1187a" in fp_lower:
        return 1.60, "XKB TS-1187A-B-A-B datasheet (1.60 mm actuator height)"
    if "qfn-60" in fp_lower or "qfn-" in fp_lower:
        return 0.90, "JEDEC QFN package max height (0.90 mm)"
    if "drc0010j" in fp_lower or "vson" in fp_lower or "uson" in fp_lower or "son" in fp_lower:
        return 1.00, "TI DRC0010J / VSON package max height (1.00 mm)"
    if "sot-523" in fp_lower:
        return 0.65, "JEDEC SOT-523 package max height (0.65 mm)"
    if "sot-23" in fp_lower:
        return 1.10, "JEDEC SOT-23 package max height (1.10 mm)"
    if "2512" in fp_lower or "2016" in fp_lower or fp_lower.startswith("inductor") or ":l_" in fp_lower:
        return 1.20, "SMD power inductor height (1.20 mm)"
    return 1.00, "default SMD package height (1.00 mm)"


def dummy_color_rgb(fp_id: str, ref: str) -> tuple[float, float, float]:
    """Return distinct RGB (0..1) for dummy STEP blocks by component category."""
    fp_lower = fp_id.lower()
    if "pcc-smd" in fp_lower:
        # Type-K yellow/amber connector accent
        return (0.86, 0.58, 0.14)
    if "usb_c" in fp_lower or "sw_push" in fp_lower or ref.startswith(("J", "SW")):
        # Metallic silver-blue for connectors/switches
        return (0.68, 0.73, 0.78)
    if ":l_" in fp_lower or ref.startswith("L"):
        # Dark slate-bronze for power inductors
        return (0.30, 0.33, 0.38)
    # Matte charcoal IC/semiconductor body
    return (0.20, 0.23, 0.27)


def make_step_box(
    name: str,
    xmin_kicad: float,
    xmax_kicad: float,
    ymin_kicad: float,
    ymax_kicad: float,
    zmin: float,
    zmax: float,
    rgb: tuple[float, float, float] = (0.25, 0.28, 0.32),
) -> str:
    """Generate a valid ISO-10303-21 STEP AP214 colored box solid.

    Note on coordinate frames:
      KiCad 2D footprint coordinates have +X East and +Y South (down).
      KiCad 3D viewer / STEP coordinates have +X East, +Y North (up), and +Z out of board.
      Therefore KiCad 2D `[ymin_kicad, ymax_kicad]` maps to STEP `[-ymax_kicad, -ymin_kicad]`.
    """
    xmin, xmax = xmin_kicad, xmax_kicad
    ymin, ymax = -ymax_kicad, -ymin_kicad
    r, g, b = rgb
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
    return f"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('OhmAI parametric dummy STEP model ({DUMMY_STEP_MARKER})'),'2;1');
FILE_NAME('{safe_name}.step','2026-10-24T00:00:00',('ohmai'),('ohmai'),'ohmai-step-box','ohmai','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN {{ 1 0 10303 214 1 1 1 1 }}'));
ENDSEC;
DATA;
#1 = APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#2);
#2 = APPLICATION_CONTEXT('core data for automotive mechanical design processes');
#3 = SHAPE_DEFINITION_REPRESENTATION(#4,#10);
#4 = PRODUCT_DEFINITION_SHAPE('','',#5);
#5 = PRODUCT_DEFINITION('design','',#6,#9);
#6 = PRODUCT_DEFINITION_FORMATION('','',#7);
#7 = PRODUCT('{safe_name}','{safe_name}','',(#8));
#8 = PRODUCT_CONTEXT('',#2,'mechanical');
#9 = PRODUCT_DEFINITION_CONTEXT('part definition',#2,'design');
#10 = ADVANCED_BREP_SHAPE_REPRESENTATION('',(#11,#15),#12);
#11 = AXIS2_PLACEMENT_3D('',#16,#17,#18);
#12 = ( GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#13)) GLOBAL_UNIT_ASSIGNED_CONTEXT((#14,#19,#20)) REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY') );
#13 = UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-07),#14,'distance_accuracy_value','confusion accuracy');
#14 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );
#19 = ( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) );
#20 = ( NAMED_UNIT(*) SOLID_ANGLE_UNIT() SI_UNIT($,.STERADIAN.) );
#16 = CARTESIAN_POINT('',(0.,0.,0.));
#17 = DIRECTION('',(0.,0.,1.));
#18 = DIRECTION('',(1.,0.,0.));
#15 = MANIFOLD_SOLID_BREP('{safe_name}',#21);
#21 = CLOSED_SHELL('',(#201,#202,#203,#204,#205,#206));
#30 = CARTESIAN_POINT('',({xmin:.4f},{ymin:.4f},{zmin:.4f}));
#31 = CARTESIAN_POINT('',({xmax:.4f},{ymin:.4f},{zmin:.4f}));
#32 = CARTESIAN_POINT('',({xmax:.4f},{ymax:.4f},{zmin:.4f}));
#33 = CARTESIAN_POINT('',({xmin:.4f},{ymax:.4f},{zmin:.4f}));
#34 = CARTESIAN_POINT('',({xmin:.4f},{ymin:.4f},{zmax:.4f}));
#35 = CARTESIAN_POINT('',({xmax:.4f},{ymin:.4f},{zmax:.4f}));
#36 = CARTESIAN_POINT('',({xmax:.4f},{ymax:.4f},{zmax:.4f}));
#37 = CARTESIAN_POINT('',({xmin:.4f},{ymax:.4f},{zmax:.4f}));
#40 = VERTEX_POINT('',#30);
#41 = VERTEX_POINT('',#31);
#42 = VERTEX_POINT('',#32);
#43 = VERTEX_POINT('',#33);
#44 = VERTEX_POINT('',#34);
#45 = VERTEX_POINT('',#35);
#46 = VERTEX_POINT('',#36);
#47 = VERTEX_POINT('',#37);
#50 = DIRECTION('',(1.,0.,0.));
#51 = DIRECTION('',(0.,1.,0.));
#52 = DIRECTION('',(0.,0.,1.));
#53 = DIRECTION('',(-1.,0.,0.));
#54 = DIRECTION('',(0.,-1.,0.));
#55 = DIRECTION('',(0.,0.,-1.));
#60 = VECTOR('',#50,1.);
#61 = VECTOR('',#51,1.);
#62 = VECTOR('',#52,1.);
#70 = LINE('',#30,#60);
#71 = LINE('',#31,#61);
#72 = LINE('',#33,#60);
#73 = LINE('',#30,#61);
#74 = LINE('',#34,#60);
#75 = LINE('',#35,#61);
#76 = LINE('',#37,#60);
#77 = LINE('',#34,#61);
#78 = LINE('',#30,#62);
#79 = LINE('',#31,#62);
#80 = LINE('',#32,#62);
#81 = LINE('',#33,#62);
#90 = EDGE_CURVE('',#40,#41,#70,.T.);
#91 = EDGE_CURVE('',#41,#42,#71,.T.);
#92 = EDGE_CURVE('',#43,#42,#72,.T.);
#93 = EDGE_CURVE('',#40,#43,#73,.T.);
#94 = EDGE_CURVE('',#44,#45,#74,.T.);
#95 = EDGE_CURVE('',#45,#46,#75,.T.);
#96 = EDGE_CURVE('',#47,#46,#76,.T.);
#97 = EDGE_CURVE('',#44,#47,#77,.T.);
#98 = EDGE_CURVE('',#40,#44,#78,.T.);
#99 = EDGE_CURVE('',#41,#45,#79,.T.);
#100 = EDGE_CURVE('',#42,#46,#80,.T.);
#101 = EDGE_CURVE('',#43,#47,#81,.T.);
#110 = ORIENTED_EDGE('',*,*,#90,.F.);
#111 = ORIENTED_EDGE('',*,*,#93,.T.);
#112 = ORIENTED_EDGE('',*,*,#92,.T.);
#113 = ORIENTED_EDGE('',*,*,#91,.F.);
#114 = EDGE_LOOP('',(#110,#111,#112,#113));
#120 = ORIENTED_EDGE('',*,*,#94,.T.);
#121 = ORIENTED_EDGE('',*,*,#95,.T.);
#122 = ORIENTED_EDGE('',*,*,#96,.F.);
#123 = ORIENTED_EDGE('',*,*,#97,.F.);
#124 = EDGE_LOOP('',(#120,#121,#122,#123));
#130 = ORIENTED_EDGE('',*,*,#90,.T.);
#131 = ORIENTED_EDGE('',*,*,#99,.T.);
#132 = ORIENTED_EDGE('',*,*,#94,.F.);
#133 = ORIENTED_EDGE('',*,*,#98,.F.);
#134 = EDGE_LOOP('',(#130,#131,#132,#133));
#140 = ORIENTED_EDGE('',*,*,#92,.F.);
#141 = ORIENTED_EDGE('',*,*,#101,.T.);
#142 = ORIENTED_EDGE('',*,*,#96,.T.);
#143 = ORIENTED_EDGE('',*,*,#100,.F.);
#144 = EDGE_LOOP('',(#140,#141,#142,#143));
#150 = ORIENTED_EDGE('',*,*,#93,.F.);
#151 = ORIENTED_EDGE('',*,*,#98,.T.);
#152 = ORIENTED_EDGE('',*,*,#97,.T.);
#153 = ORIENTED_EDGE('',*,*,#101,.F.);
#154 = EDGE_LOOP('',(#150,#151,#152,#153));
#160 = ORIENTED_EDGE('',*,*,#91,.T.);
#161 = ORIENTED_EDGE('',*,*,#100,.T.);
#162 = ORIENTED_EDGE('',*,*,#95,.F.);
#163 = ORIENTED_EDGE('',*,*,#99,.F.);
#164 = EDGE_LOOP('',(#160,#161,#162,#163));
#170 = AXIS2_PLACEMENT_3D('',#30,#55,#50);
#171 = AXIS2_PLACEMENT_3D('',#34,#52,#50);
#172 = AXIS2_PLACEMENT_3D('',#30,#54,#50);
#173 = AXIS2_PLACEMENT_3D('',#33,#51,#50);
#174 = AXIS2_PLACEMENT_3D('',#30,#53,#51);
#175 = AXIS2_PLACEMENT_3D('',#31,#50,#51);
#180 = PLANE('',#170);
#181 = PLANE('',#171);
#182 = PLANE('',#172);
#183 = PLANE('',#173);
#184 = PLANE('',#174);
#185 = PLANE('',#175);
#115 = FACE_OUTER_BOUND('',#114,.T.);
#125 = FACE_OUTER_BOUND('',#124,.T.);
#135 = FACE_OUTER_BOUND('',#134,.T.);
#145 = FACE_OUTER_BOUND('',#144,.T.);
#155 = FACE_OUTER_BOUND('',#154,.T.);
#165 = FACE_OUTER_BOUND('',#164,.T.);
#201 = ADVANCED_FACE('',(#115),#180,.T.);
#202 = ADVANCED_FACE('',(#125),#181,.T.);
#203 = ADVANCED_FACE('',(#135),#182,.T.);
#204 = ADVANCED_FACE('',(#145),#183,.T.);
#205 = ADVANCED_FACE('',(#155),#184,.T.);
#206 = ADVANCED_FACE('',(#165),#185,.T.);
#300 = MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION('',(#301),#12);
#301 = STYLED_ITEM('color',(#302),#15);
#302 = PRESENTATION_STYLE_ASSIGNMENT((#303));
#303 = SURFACE_STYLE_USAGE(.BOTH.,#304);
#304 = SURFACE_SIDE_STYLE('',(#305));
#305 = SURFACE_STYLE_FILL_AREA(#306);
#306 = FILL_AREA_STYLE('',(#307));
#307 = FILL_AREA_STYLE_COLOUR('',#308);
#308 = COLOUR_RGB('',{r:.3f},{g:.3f},{b:.3f});
ENDSEC;
END-ISO-10303-21;
"""


def _is_existing_official_step(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        head = path.read_text(errors="ignore")[:512]
        return DUMMY_STEP_MARKER not in head and "ISO-10303-21" in head
    except Exception:
        return False


def _fetch_official_step(tag: str, rel_path: str, dest: Path) -> bool:
    if _is_existing_official_step(dest):
        return True
    url = PACKAGES3D_RAW_URL.format(tag=tag, rel_path=rel_path)
    req = urllib.request.Request(url, headers={"User-Agent": "ohmai-kicad-libs/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if not data or b"ISO-10303-21" not in data[:512]:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False


def sync_project_models(
    project: Project,
    *,
    dummy_missing: bool = True,
    stage_pcb: Path | None = None,
) -> dict[str, Any]:
    """Fetch project-referenced 3D `.step` models into `<project>/out/3dmodels/` and flag/dummy missing ones."""
    pcb_path = project.pcb
    if not pcb_path.exists():
        raise FileNotFoundError(f"PCB file not found: {pcb_path}")

    tag = str(project.config.get("kicad", {}).get("libraries", "9.0.9.1"))
    out_dir = project_models_dir(project)
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["KICAD9_3DMODEL_DIR"] = str(out_dir)

    pcb_text = pcb_path.read_text()
    root = parse(pcb_text)
    ref_to_bom, constraints = _load_bom_and_constraints(project)

    by_fp: dict[str, dict[str, Any]] = {}
    missing_model_entry_refs: dict[str, str] = {}

    for fp in find(root, "footprint"):
        fp_id = fp[1] if len(fp) > 1 else "?"
        ref = "?"
        val = "?"
        for prop in find(fp, "property"):
            if len(prop) >= 3 and prop[1] == "Reference":
                ref = prop[2]
            elif len(prop) >= 3 and prop[1] == "Value":
                val = prop[2]

        models = find(fp, "model")
        model_rel = None
        if models and len(models[0]) > 1:
            raw_path = str(models[0][1])
            for prefix in ("${KICAD9_3DMODEL_DIR}/", "${KICAD8_3DMODEL_DIR}/", "${KISYS3DMOD}/"):
                if raw_path.startswith(prefix):
                    model_rel = raw_path[len(prefix) :]
                    break
            if model_rel is None:
                model_rel = raw_path

        entry = by_fp.get(fp_id)
        if entry is None:
            xmin, xmax, ymin, ymax, bbox_layer = footprint_bbox_xy(fp)
            entry = {
                "footprint": fp_id,
                "refs": [],
                "value_sample": val,
                "referenced_model": model_rel,
                "bbox_kicad_mm": {
                    "xmin": round(xmin, 3),
                    "xmax": round(xmax, 3),
                    "ymin": round(ymin, 3),
                    "ymax": round(ymax, 3),
                    "dx": round(xmax - xmin, 3),
                    "dy": round(ymax - ymin, 3),
                    "layer": bbox_layer,
                },
                "fp_node": fp,
            }
            by_fp[fp_id] = entry
        entry["refs"].append(ref)

    fetched_official: list[dict[str, Any]] = []
    flagged_missing: list[dict[str, Any]] = []
    bare_copper: list[dict[str, Any]] = []

    for fp_id, info in sorted(by_fp.items()):
        refs = sorted(info["refs"], key=lambda r: (re.sub(r"\d+", "", r), int(re.findall(r"\d+", r)[0]) if re.findall(r"\d+", r) else 0))
        first_ref = refs[0]
        model_rel = info["referenced_model"]
        bbox = info["bbox_kicad_mm"]

        if is_non_component_footprint(fp_id, first_ref):
            bare_copper.append(
                {
                    "footprint": fp_id,
                    "refs": refs,
                    "count": len(refs),
                    "status": "bare_copper_feature",
                }
            )
            continue

        if model_rel:
            dest = out_dir / model_rel
            if _fetch_official_step(tag, model_rel, dest):
                fetched_official.append(
                    {
                        "footprint": fp_id,
                        "refs": refs,
                        "count": len(refs),
                        "model_rel": model_rel,
                        "size_bytes": dest.stat().st_size,
                        "status": "fetched_official",
                    }
                )
                continue
            reason = f"referenced `{model_rel}` returned 404 in kicad-packages3D@{tag}"
            flag_type = "missing_upstream_step"
            target_rel = model_rel
        else:
            lib, _, fp_name = fp_id.partition(":")
            target_rel = f"{lib}.3dshapes/{fp_name}.step"
            reason = "no `(model ...)` entry in footprint"
            flag_type = "missing_model_entry"
            for r in refs:
                missing_model_entry_refs[r] = f"${{KICAD9_3DMODEL_DIR}}/{target_rel}"

        height_mm, height_src = infer_component_height_mm(
            project, first_ref, fp_id, info["value_sample"], ref_to_bom, constraints
        )
        dest = out_dir / target_rel
        if dummy_missing:
            dest.parent.mkdir(parents=True, exist_ok=True)
            rgb = dummy_color_rgb(fp_id, first_ref)
            step_txt = make_step_box(
                fp_id.split(":")[-1],
                bbox["xmin"],
                bbox["xmax"],
                bbox["ymin"],
                bbox["ymax"],
                0.0,
                height_mm,
                rgb=rgb,
            )
            dest.write_text(step_txt)

        flagged_missing.append(
            {
                "footprint": fp_id,
                "refs": refs,
                "count": len(refs),
                "value": info["value_sample"],
                "flag": flag_type,
                "reason": reason,
                "dummy_model_rel": target_rel if dummy_missing else None,
                "dimensions_mm": {
                    "dx": bbox["dx"],
                    "dy": bbox["dy"],
                    "dz": round(height_mm, 3),
                    "bbox_layer": bbox["layer"],
                    "height_source": height_src,
                },
                "size_bytes": dest.stat().st_size if dummy_missing and dest.exists() else 0,
            }
        )

    if stage_pcb is not None:
        staged_text = _inject_models_into_pcb_text(pcb_text, missing_model_entry_refs)
        stage_pcb.parent.mkdir(parents=True, exist_ok=True)
        stage_pcb.write_text(staged_text)

    total_official_bytes = sum(x["size_bytes"] for x in fetched_official)
    total_dummy_bytes = sum(x["size_bytes"] for x in flagged_missing)
    manifest = {
        "kicad_libraries_tag": tag,
        "models_dir": str(out_dir.relative_to(project.root)) if out_dir.is_relative_to(project.root) else str(out_dir),
        "total_official_files": len(fetched_official),
        "total_official_bytes": total_official_bytes,
        "total_flagged_missing": len(flagged_missing),
        "total_dummy_bytes": total_dummy_bytes,
        "fetched_official": fetched_official,
        "flagged_missing": flagged_missing,
        "bare_copper_features": bare_copper,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def _inject_models_into_pcb_text(pcb_text: str, ref_to_model_path: dict[str, str]) -> str:
    """Inject `(model ...)` blocks into footprints by Reference in a copy of `.kicad_pcb` text."""
    if not ref_to_model_path:
        return pcb_text

    # Walk top-level `(footprint ...)` blocks by parenthesis depth so we preserve exact formatting.
    out_chunks: list[str] = []
    idx = 0
    n = len(pcb_text)
    while idx < n:
        m_fp = re.search(r"(?m)^[ \t]*\(footprint\s", pcb_text[idx:])
        fp_start = (idx + m_fp.start()) if m_fp else -1
        if fp_start == -1:
            out_chunks.append(pcb_text[idx:])
            break
        out_chunks.append(pcb_text[idx:fp_start])
        # Find matching closing paren for this footprint block
        depth = 0
        in_str = False
        esc = False
        pos = pcb_text.find("(", fp_start)
        while pos < n:
            ch = pcb_text[pos]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        pos += 1
                        break
            pos += 1
        fp_block = pcb_text[fp_start:pos]
        m_ref = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', fp_block)
        if m_ref and m_ref.group(1) in ref_to_model_path and "\n\t\t(model " not in fp_block:
            model_path = ref_to_model_path[m_ref.group(1)]
            model_sexp = (
                f'\t\t(model "{model_path}"\n'
                f"\t\t\t(offset\n\t\t\t\t(xyz 0 0 0)\n\t\t\t)\n"
                f"\t\t\t(scale\n\t\t\t\t(xyz 1 1 1)\n\t\t\t)\n"
                f"\t\t\t(rotate\n\t\t\t\t(xyz 0 0 0)\n\t\t\t)\n"
                f"\t\t)\n"
            )
            # Insert right before the closing `\t)` of the footprint block
            if fp_block.endswith("\n\t)"):
                fp_block = fp_block[:-2] + model_sexp + "\t)"
        out_chunks.append(fp_block)
        idx = pos
    return "".join(out_chunks)

ensure_project_models = sync_project_models
