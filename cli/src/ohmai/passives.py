"""Autonomous passive package swap helper (`ohmai passives swap` / `ohmai parts swap`).

Allows resizing standard passives (R, C, L, FB across 0402..1206 / 2016..3030) during Schematic and Layout
without stopping for an owner gate, while validating constraints and atomically updating bom/bom.csv,
hardware/**/*.kicad_sch, hardware/gen/layout/*.yaml, and hardware/*.kicad_pcb.
"""

from __future__ import annotations

from ohmai.netlist import parse_value

import csv
import io
import json
import re
from pathlib import Path

import yaml

from ohmai import sexp
from ohmai.project import Project

PASSIVE_REF_RE = re.compile(r"^(R|C|L|FB)\d+$")
ALLOWED_PACKAGES = {
    "0402": "1005",
    "0603": "1608",
    "0805": "2012",
    "1206": "3216",
    "2016": "2016",
    "2520": "2520",
    "3030": "3030",
}


def default_kicad_footprint(ref: str, package: str) -> str:
    m = PASSIVE_REF_RE.match(ref)
    kind = m.group(1) if m else "R"
    metric = ALLOWED_PACKAGES.get(package, package)
    if kind == "R":
        return f"Resistor_SMD:R_{package}_{metric}Metric"
    if kind == "C":
        return f"Capacitor_SMD:C_{package}_{metric}Metric"
    return f"Inductor_SMD:L_{package}_{metric}Metric"


def _split_refs(raw: str) -> tuple[list[str], str]:
    sep = ", " if "," in raw else " "
    refs = [r.strip() for r in re.split(r"[,\s]+", raw.strip()) if r.strip()]
    return refs, sep


def _validate_passive(ref: str, package: str) -> str:
    m = PASSIVE_REF_RE.match(ref)
    if not m:
        raise ValueError(
            f"{ref!r}: only passive components (R, C, L, FB) may be autonomously swapped; "
            "active ICs, connectors, crystals, and semiconductors require owner approval"
        )
    pkg = package.strip()
    if pkg not in ALLOWED_PACKAGES and not any(k in pkg for k in ALLOWED_PACKAGES):
        raise ValueError(
            f"package {package!r} is outside the standard autonomous passive range "
            f"({', '.join(sorted(ALLOWED_PACKAGES))})"
        )
    return m.group(1)


def _check_constraints_for_swap(
    proj: Project,
    ref: str,
    value_str: str | None,
    effective_value_str: str | None,
) -> list[str]:
    """Check if any constraint file bounds this passive's value / effective value."""
    warnings: list[str] = []
    cdir = proj.root / "parts" / "constraints"
    if not cdir.is_dir():
        return warnings
    val = parse_value(value_str) if value_str else None
    eff = parse_value(effective_value_str) if effective_value_str else None
    for yf in sorted(cdir.glob("*.yaml")):
        if yf.name.endswith(".example.yaml"):
            continue
        data = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
        for rp in data.get("required_passives") or []:
            rp_refs = rp.get("refs") or ([] if not rp.get("ref") else [rp["ref"]])
            if ref not in rp_refs:
                continue
            vspec = rp.get("value") or {}
            vmin = parse_value(f"{vspec['min']} {vspec.get('unit', '')}") if "min" in vspec else None
            vmax = parse_value(f"{vspec['max']} {vspec.get('unit', '')}") if "max" in vspec else None
            check_v = eff if eff is not None else val
            if check_v is not None and vmin is not None and check_v.si < vmin.si * (1 - 1e-6):
                raise ValueError(
                    f"{ref}: value {check_v.raw} is below {yf.name} {rp.get('id', '')} min {vmin.raw}"
                )
            if val is not None and vmax is not None and val.si > vmax.si * (1 + 1e-6):
                raise ValueError(
                    f"{ref}: nominal value {val.raw} exceeds {yf.name} {rp.get('id', '')} max {vmax.raw}"
                )
    return warnings


def _update_bom(
    bom_path: Path,
    ref: str,
    package: str,
    mpn: str | None,
    manufacturer: str | None,
    lcsc: str | None,
    value: str | None,
    rating_notes: str | None,
    note: str | None,
    dry_run: bool,
) -> dict:
    text = bom_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    comments = [ln for ln in lines if ln.lstrip().startswith("#")]
    body = "".join(ln for ln in lines if not ln.lstrip().startswith("#"))
    reader = csv.DictReader(io.StringIO(body))
    fieldnames = list(reader.fieldnames or [])
    rows = list(reader)

    new_rows: list[dict] = []
    matched_info: dict | None = None
    ref_col = "refs" if "refs" in fieldnames else ("ref" if "ref" in fieldnames else "refs")
    kicad_fp = default_kicad_footprint(ref, package)
    for row in rows:
        refs, sep = _split_refs(row.get(ref_col, ""))
        if ref not in refs:
            new_rows.append(row)
            continue
        old_pkg = row.get("package") or row.get("footprint", "")
        old_mpn = row.get("mpn", "")
        target = dict(row)
        target[ref_col] = ref
        target["qty"] = "1"
        if "package" in fieldnames and "footprint" in fieldnames:
            target["package"] = package
            target["footprint"] = kicad_fp
        elif "footprint" in fieldnames:
            target["footprint"] = package
        elif "package" in fieldnames:
            target["package"] = package
        if mpn is not None:
            target["mpn"] = mpn
        if manufacturer is not None:
            target["manufacturer"] = manufacturer
        if lcsc is not None:
            if "supplier_pn" in fieldnames:
                target["supplier_pn"] = lcsc
            elif "lcsc" in fieldnames:
                target["lcsc"] = lcsc
        if value is not None:
            target["value"] = value
        if rating_notes is not None and "rating_notes" in fieldnames:
            target["rating_notes"] = rating_notes
        if note is not None and "notes" in fieldnames:
            prev = target.get("notes", "")
            target["notes"] = f"{prev}; {note}".strip("; ") if prev else note

        if len(refs) == 1:
            new_rows.append(target)
        else:
            rem = [r for r in refs if r != ref]
            kept = dict(row)
            kept[ref_col] = sep.join(rem)
            kept["qty"] = str(len(rem))
            new_rows.append(kept)
            new_rows.append(target)
        matched_info = {
            "old_package": old_pkg,
            "new_package": package,
            "old_mpn": old_mpn,
            "new_mpn": target.get("mpn", ""),
            "value": target.get("value", ""),
            "split_from_refs": refs if len(refs) > 1 else None,
        }

    if matched_info is None:
        raise KeyError(f"{ref!r} not found in {bom_path}")

    if not dry_run:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(new_rows)
        bom_path.write_text("".join(comments) + buf.getvalue(), encoding="utf-8")
    return matched_info


def _update_sch_files(
    hw_dir: Path,
    ref: str,
    kicad_fp: str,
    mpn: str | None,
    lcsc: str | None,
    value: str | None,
    dry_run: bool,
) -> list[str]:
    updated: list[str] = []
    if not hw_dir.is_dir():
        return updated
    for sch in sorted(hw_dir.rglob("*.kicad_sch")):
        tree = sexp.parse(sch.read_text(encoding="utf-8"))
        changed = False
        for sym in sexp.find(tree, "symbol"):
            # Skip lib_symbols definitions
            if sym and len(sym) > 1 and isinstance(sym[1], str) and ":" in sym[1]:
                continue
            sref = ""
            for prop in sexp.find(sym, "property"):
                if len(prop) > 2 and prop[1] == "Reference":
                    sref = prop[2]
            if not sref:
                for inst in sexp.find(sym, "instances"):
                    for p in sexp.find(inst, "path"):
                        rnode = sexp.first(p, "reference")
                        if rnode and len(rnode) > 1:
                            sref = rnode[1]
            if sref != ref:
                continue
            props = {p[1]: p for p in sexp.find(sym, "property") if len(p) > 2}
            if "Footprint" in props and props["Footprint"][2] != kicad_fp:
                props["Footprint"][2] = kicad_fp
                changed = True
            if mpn is not None and "MPN" in props and props["MPN"][2] != mpn:
                props["MPN"][2] = mpn
                changed = True
            if lcsc is not None and "LCSC" in props and props["LCSC"][2] != lcsc:
                props["LCSC"][2] = lcsc
                changed = True
            if value is not None and "Value" in props and props["Value"][2] != value:
                props["Value"][2] = value
                changed = True
        if changed:
            updated.append(str(sch))
            if not dry_run:
                sch.write_text(sexp.dumps(tree) + "\n", encoding="utf-8")
    return updated


def _update_pcb_files(hw_dir: Path, ref: str, kicad_fp: str, dry_run: bool) -> list[str]:
    updated: list[str] = []
    if not hw_dir.is_dir():
        return updated
    for pcb in sorted(hw_dir.glob("*.kicad_pcb")):
        tree = sexp.parse(pcb.read_text(encoding="utf-8"))
        changed = False
        for fp in sexp.find(tree, "footprint"):
            fref = ""
            for prop in sexp.find(fp, "property"):
                if len(prop) > 2 and prop[1] == "Reference":
                    fref = prop[2]
            if not fref:
                fp_text = [t for t in sexp.find(fp, "fp_text") if len(t) > 2 and t[1] == "reference"]
                fref = fp_text[0][2] if fp_text else ""
            if fref != ref:
                continue
            if len(fp) > 1 and isinstance(fp[1], str) and fp[1] != kicad_fp:
                fp[1] = kicad_fp
                changed = True
            for prop in sexp.find(fp, "property"):
                if len(prop) > 2 and prop[1] == "Footprint" and prop[2] != kicad_fp:
                    prop[2] = kicad_fp
                    changed = True
        if changed:
            updated.append(str(pcb))
            if not dry_run:
                pcb.write_text(sexp.dumps(tree) + "\n", encoding="utf-8")
    return updated


def swap_passive(
    proj: Project,
    ref: str,
    package: str,
    *,
    mpn: str | None = None,
    manufacturer: str | None = None,
    lcsc: str | None = None,
    footprint: str | None = None,
    value: str | None = None,
    effective_value: str | None = None,
    rating_notes: str | None = None,
    note: str | None = None,
    update_pcb: bool = True,
    dry_run: bool = False,
) -> dict:
    """Validate and perform an autonomous passive package swap across BOM, schematic, and PCB."""
    _validate_passive(ref, package)
    kicad_fp = footprint or default_kicad_footprint(ref, package)
    bom_path = proj.root / "bom" / "bom.csv"
    if not bom_path.exists():
        raise FileNotFoundError(f"BOM not found: {bom_path}")

    # Validate constraints first before writing any file
    _check_constraints_for_swap(proj, ref, value, effective_value)

    bom_info = _update_bom(
        bom_path,
        ref,
        package,
        mpn=mpn,
        manufacturer=manufacturer,
        lcsc=lcsc,
        value=value,
        rating_notes=rating_notes,
        note=note,
        dry_run=dry_run,
    )
    # Also validate existing BOM value if value was not overridden
    if value is None and bom_info.get("value"):
        _check_constraints_for_swap(proj, ref, bom_info["value"], effective_value)

    hw_dir = proj.root / "hardware"
    sch_files = _update_sch_files(hw_dir, ref, kicad_fp, mpn, lcsc, value, dry_run)
    pcb_files = _update_pcb_files(hw_dir, ref, kicad_fp, dry_run) if update_pcb else []

    files = [str(bom_path.relative_to(proj.root))]
    files += [str(Path(p).relative_to(proj.root)) for p in sch_files + pcb_files]
    return {
        "ok": True,
        "errors": [],
        "ref": ref,
        "old_package": bom_info["old_package"],
        "new_package": bom_info["new_package"],
        "old_mpn": bom_info["old_mpn"],
        "new_mpn": bom_info["new_mpn"],
        "footprint": kicad_fp,
        "new_footprint": kicad_fp,
        "split_from_refs": bom_info["split_from_refs"],
        "files_updated": files,
        "dry_run": dry_run,
    }


def add_subparser(sub) -> None:
    p = sub.add_parser(
        "passives",
        help="autonomous passive package swap helper (R, C, L, FB within 0402..1206 / 2016..3030)",
    )
    ps = p.add_subparsers(dest="passives_cmd", required=True)
    add_swap_parser(ps)


def add_swap_parser(sub) -> None:
    sp = sub.add_parser(
        "swap",
        help="resize/swap a passive component across bom/bom.csv, .kicad_sch, and .kicad_pcb",
    )
    sp.add_argument("ref", help="passive reference designator (e.g. C20, R5, L2)")
    sp.add_argument("--package", required=True, help="target standard package (0402, 0603, 0805, 1206, 2016, 2520, 3030)")
    sp.add_argument("--mpn", help="new manufacturer part number")
    sp.add_argument("--manufacturer", help="new manufacturer name")
    sp.add_argument("--lcsc", help="new LCSC/JLCPCB part number (e.g. C15850)")
    sp.add_argument("--footprint", help="explicit KiCad library:footprint (default derived from ref + package)")
    sp.add_argument("--value", help="updated value string if changed")
    sp.add_argument("--effective-value", help="effective value at operating DC bias (e.g. '5.0 uF') to check against constraints min")
    sp.add_argument("--rating-notes", help="updated rating / DC-bias derating notes")
    sp.add_argument("--note", help="note to append to the BOM notes column")
    sp.add_argument("--no-pcb", action="store_true", help="do not modify hardware/*.kicad_pcb")
    sp.add_argument("--dry-run", action="store_true", help="validate and show changes without writing files")
    sp.add_argument("--json", action="store_true", help="emit JSON summary")
    sp.set_defaults(fn=_cmd_swap)


def _cmd_swap(args) -> int:
    from ohmai.project import find_project
    return run_cli(args, find_project())


add_parser = add_subparser


def run_cli(args, proj: Project) -> int:
    try:
        res = swap_passive(
            proj,
            args.ref,
            args.package,
            mpn=args.mpn,
            manufacturer=args.manufacturer,
            lcsc=args.lcsc,
            footprint=args.footprint,
            value=args.value,
            effective_value=args.effective_value,
            rating_notes=args.rating_notes,
            note=args.note,
            update_pcb=not args.no_pcb,
            dry_run=args.dry_run,
        )
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"FAIL: {exc}")
        return 1
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        prefix = "[dry-run] " if res["dry_run"] else ""
        print(
            f"{prefix}PASS: {res['ref']} {res['old_package']} -> {res['new_package']} "
            f"(MPN {res['old_mpn']} -> {res['new_mpn']}, footprint {res['footprint']}); "
            f"files: {', '.join(res['files_updated'])}"
        )
    return 0
