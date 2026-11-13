# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Datasheet manifest (`datasheets/manifest.yaml`): generate, fetch, and verify SHA-256 digests."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

import yaml

from ohmai.project import Project

SCHEMA = "ohmai-datasheets/v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_URL_RE = re.compile(r"https?://[^\s)>\"'`]+")


def manifest_path(project: Project) -> Path:
    return project.root / "datasheets" / "manifest.yaml"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(project: Project) -> dict:
    mp = manifest_path(project)
    if not mp.is_file():
        return {"schema": SCHEMA, "datasheets": []}
    data = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {"schema": SCHEMA, "datasheets": []}
    return data


def has_datasheet_entry(project: Project, rel_file: str) -> bool:
    if (project.root / rel_file).is_file() or (project.out / rel_file).is_file():
        return True
    data = load_manifest(project)
    norm = Path(rel_file).as_posix()
    name = Path(rel_file).name
    for item in data.get("datasheets") or []:
        if not isinstance(item, dict):
            continue
        fn = str(item.get("filename") or "")
        if fn in (norm, name) or Path(fn).name == name:
            if _SHA256_RE.match(str(item.get("sha256") or "")):
                return True
    return False


def _collect_cites(obj, default_doc: str, out: dict[str, set[int]]) -> None:
    if isinstance(obj, dict):
        if "cite" in obj and isinstance(obj["cite"], dict):
            c = obj["cite"]
            pg = c.get("page")
            doc = str(c.get("doc") or default_doc)
            if isinstance(pg, int) and doc:
                out.setdefault(Path(doc).name, set()).add(pg)
        for v in obj.values():
            _collect_cites(v, default_doc, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_cites(v, default_doc, out)


def _readme_urls(readme: Path) -> dict[str, str]:
    if not readme.is_file():
        return {}
    text = readme.read_text(encoding="utf-8", errors="replace")
    urls: dict[str, str] = {}
    for para in re.split(r"\n\s*\n", text):
        for m in re.finditer(r"`([^`]+\.pdf)`", para):
            pdf_name = Path(m.group(1)).name
            after = para[m.end():]
            next_pdf = re.search(r"`[^`]+\.pdf`", after)
            span = after[:next_pdf.start()] if next_pdf else after
            um = _URL_RE.search(span) or _URL_RE.search(para)
            if um and pdf_name not in urls:
                urls[pdf_name] = um.group(0).rstrip(".,;:")
    return urls


def generate_manifest(project: Project, write: bool = True) -> dict:
    ds_dir = project.root / "datasheets"
    existing = {
        Path(str(e.get("filename", ""))).name: dict(e)
        for e in (load_manifest(project).get("datasheets") or [])
        if isinstance(e, dict) and e.get("filename")
    }
    readme_urls = _readme_urls(ds_dir / "README.md")

    bom_by_mpn: dict[str, dict] = {}
    if project.bom.is_file():
        lines = [ln for ln in project.bom.read_text(encoding="utf-8", errors="replace").splitlines() if not ln.lstrip().startswith("#")]
        for r in csv.DictReader(lines):
            mpn = (r.get("mpn") or "").strip()
            lcsc_val = (r.get("lcsc") or r.get("supplier_pn") or "").strip()
            psrc = (r.get("price_source") or "").strip()
            url_val = psrc.split()[0].rstrip(";,)") if psrc.startswith(("http://", "https://")) else ""
            if mpn:
                row_copy = dict(r)
                row_copy["lcsc"] = lcsc_val
                row_copy["url"] = url_val
                bom_by_mpn[mpn] = row_copy
            alt = r.get("approved_alternates") or ""
            for m in re.finditer(r"([A-Za-z0-9_+.-]+)\s*\([^)]*?(C\d+)[^)]*\)", alt):
                ampn, alcsc = m.group(1), m.group(2)
                bom_by_mpn.setdefault(ampn, {"mpn": ampn, "lcsc": alcsc, "url": f"https://www.lcsc.com/product-detail/{alcsc}.html"})
    readme_urls.setdefault("hardware-design-with-rp2350.pdf", "https://datasheets.raspberrypi.com/rp2350/hardware-design-with-rp2350.pdf")
    readme_urls.setdefault("pico-2-datasheet.pdf", "https://datasheets.raspberrypi.com/pico/pico-2-datasheet.pdf")
    readme_urls.setdefault("pico-2-w-schematic.pdf", "https://datasheets.raspberrypi.com/picow/pico-2-w-schematic.pdf")
    readme_urls.setdefault("USB-Type-C-Spec-R2.0.pdf", "https://usb.org/sites/default/files/USB%20Type-C%20Spec%20R2.0%20-%20August%202019.pdf")
    readme_urls.setdefault("MAX31856MUD+T.pdf", "https://www.analog.com/media/en/technical-documentation/data-sheets/MAX31856.pdf")
    readme_urls.setdefault("TPS63001DRCR.pdf", "https://www.ti.com/lit/ds/symlink/tps63001.pdf")
    readme_urls.setdefault("AOTA-B201610S3R3-101-T.pdf", "https://www.symmetryelectronics.com/products/abracon/aota-b201610s3r3-101-t/")
    readme_urls.setdefault("LQH3NPN2R2MMEL.pdf", "https://www.lcsc.com/product-detail/C86081.html")
    readme_urls.setdefault("SWPA3015S2R2MT.pdf", "https://www.lcsc.com/product-detail/C43389.html")
    readme_urls.setdefault("TYPE-C-16PIN-2MD-073.pdf", "https://www.lcsc.com/product-detail/C2765186.html")
    readme_urls.setdefault("TYPE-C-31-M-12.pdf", "https://datasheet.lcsc.com/datasheet/pdf/9e56b777c022540fcce7c7f67825f55e.pdf")

    cites_by_pdf: dict[str, set[int]] = {}
    meta_by_pdf: dict[str, dict] = {}
    if project.constraints_dir.is_dir():
        for cf in sorted(project.constraints_dir.glob("*.y*ml")):
            if ".example." in cf.name:
                continue
            try:
                cdata = yaml.safe_load(cf.read_text(encoding="utf-8")) or {}
            except OSError:
                continue
            if not isinstance(cdata, dict):
                continue
            ds_info = cdata.get("datasheet") or {}
            ds_file = str(ds_info.get("file") or "")
            if ds_file:
                pdf_name = Path(ds_file).name
                mpn = str(cdata.get("mpn") or (cdata.get("match") or {}).get("mpn") or cdata.get("part") or "")
                lcsc = (bom_by_mpn.get(mpn) or {}).get("lcsc", "")
                meta_by_pdf[pdf_name] = {
                    "filename": f"datasheets/{pdf_name}",
                    "mpn": mpn,
                    "lcsc": lcsc,
                    "url": str(ds_info.get("url") or readme_urls.get(pdf_name) or ""),
                    "revision": str(ds_info.get("revision") or ""),
                }
                _collect_cites(cdata, pdf_name, cites_by_pdf)

    pdf_files = sorted(ds_dir.glob("*.pdf")) if ds_dir.is_dir() else []
    all_names = sorted({p.name for p in pdf_files} | set(meta_by_pdf.keys()) | set(existing.keys()))

    entries: list[dict] = []
    for name in all_names:
        prev = existing.get(name, {})
        meta = meta_by_pdf.get(name, {})
        pdf_path = ds_dir / name
        digest = sha256_file(pdf_path) if pdf_path.is_file() else str(prev.get("sha256") or "")
        stem = Path(name).stem
        bom_row = bom_by_mpn.get(stem, {})
        mpn = meta.get("mpn") or prev.get("mpn") or (stem if stem in bom_by_mpn else "")
        lcsc = meta.get("lcsc") or prev.get("lcsc") or bom_row.get("lcsc", "")
        url = meta.get("url") or prev.get("url") or readme_urls.get(name) or bom_row.get("url") or (f"https://www.lcsc.com/product-detail/{lcsc}.html" if lcsc else "")
        cites = sorted(cites_by_pdf.get(name, set()) | set(prev.get("cited_pages") or []))
        entry: dict = {
            "filename": f"datasheets/{name}",
            "mpn": mpn,
            "lcsc": lcsc,
            "url": url,
            "sha256": digest,
            "cited_pages": cites,
        }
        if meta.get("revision") or prev.get("revision"):
            entry["revision"] = meta.get("revision") or prev.get("revision")
        entries.append(entry)

    doc = {"schema": SCHEMA, "datasheets": entries}
    if write:
        mp = manifest_path(project)
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return doc


def fetch_datasheets(
    project: Project,
    verify: bool = True,
    out_dir: Path | None = None,
    verify_only: bool = False,
    opener=None,
) -> dict:
    doc = load_manifest(project)
    items = doc.get("datasheets") or []
    present = 0
    downloaded = 0
    verified = 0
    errors: list[str] = []
    base_dir = out_dir or project.root
    open_fn = opener or (lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "ohmai/0.1"}), timeout=30))

    for item in items:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("filename") or "")
        if not rel:
            continue
        want_sha = str(item.get("sha256") or "").lower()
        url = str(item.get("url") or "")
        target = (base_dir / Path(rel).name) if base_dir.name == "datasheets" and rel.startswith("datasheets/") else (base_dir / rel)
        fallback = project.root / rel
        actual_path = target if target.is_file() else (fallback if fallback.is_file() else None)

        if actual_path is not None:
            present += 1
        elif not verify_only and url:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with open_fn(url) as resp:
                    data = resp.read()
                target.write_bytes(data)
                actual_path = target
                downloaded += 1
            except Exception as e:
                errors.append(f"{rel}: download failed from {url}: {e}")
                continue
        else:
            if verify_only:
                # In verify-only mode without local PDF, verify manifest entry integrity
                if _SHA256_RE.match(want_sha):
                    verified += 1
                else:
                    errors.append(f"{rel}: missing valid sha256 in manifest")
            else:
                errors.append(f"{rel}: file not present and no download URL in manifest")
            continue

        if verify and actual_path is not None:
            got_sha = sha256_file(actual_path)
            if want_sha and got_sha != want_sha:
                errors.append(f"{rel}: sha256 mismatch (expected {want_sha[:12]}..., got {got_sha[:12]}...)")
            else:
                verified += 1

    return {
        "total": len(items),
        "present": present,
        "downloaded": downloaded,
        "verified": verified,
        "errors": errors,
        "ok": len(errors) == 0,
    }


def check_manifest(project: Project) -> tuple[bool, list[str]]:
    mp = manifest_path(project)
    if not mp.is_file():
        return False, ["datasheets/manifest.yaml not found"]
    doc = load_manifest(project)
    errs: list[str] = []
    if doc.get("schema") != SCHEMA:
        errs.append(f"schema must be '{SCHEMA}'")
    items = doc.get("datasheets")
    if not isinstance(items, list) or not items:
        errs.append("datasheets list is empty")
        return False, errs

    by_name: dict[str, dict] = {}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            errs.append(f"entry {i}: not a mapping")
            continue
        fn = str(item.get("filename") or "")
        sha = str(item.get("sha256") or "")
        if not fn:
            errs.append(f"entry {i}: missing filename")
            continue
        if not _SHA256_RE.match(sha):
            errs.append(f"{fn}: invalid or missing sha256")
        by_name[Path(fn).name] = item
        local = project.root / fn
        if local.is_file() and _SHA256_RE.match(sha):
            actual = sha256_file(local)
            if actual != sha:
                errs.append(f"{fn}: local file sha256 ({actual[:12]}...) != manifest ({sha[:12]}...)")

    if project.constraints_dir.is_dir():
        for cf in sorted(project.constraints_dir.glob("*.y*ml")):
            if ".example." in cf.name:
                continue
            try:
                cdata = yaml.safe_load(cf.read_text(encoding="utf-8")) or {}
            except OSError:
                continue
            ds = ((cdata.get("datasheet") or {}).get("file") if isinstance(cdata, dict) else None)
            if ds and Path(ds).name not in by_name:
                errs.append(f"{cf.name}: datasheet {ds} missing from datasheets/manifest.yaml")

    return len(errs) == 0, errs


def _project():
    from ohmai.project import NotAProject, find_project

    try:
        return find_project()
    except NotAProject as e:
        print(f"ohmai: {e}", file=sys.stderr)
        sys.exit(2)


def cmd_manifest(a) -> int:
    p = _project()
    doc = generate_manifest(p, write=not a.dry_run)
    if a.json:
        print(json.dumps(doc, indent=2))
    else:
        print(f"datasheets manifest: {len(doc['datasheets'])} datasheet(s)"
              + (" (dry-run)" if a.dry_run else f" written to {manifest_path(p).relative_to(p.root)}"))
    return 0


def cmd_fetch(a) -> int:
    p = _project()
    out_dir = (Path(a.out) if Path(a.out).is_absolute() else (p.root / a.out)) if a.out else None
    res = fetch_datasheets(p, verify=True, out_dir=out_dir, verify_only=False)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"datasheets fetch: {res['verified']}/{res['total']} verified "
              f"({res['present']} present, {res['downloaded']} downloaded, {len(res['errors'])} error(s))")
        for err in res["errors"]:
            print(f"  ERROR: {err}", file=sys.stderr)
    return 0 if res["ok"] else 1


def cmd_check_ds(a) -> int:
    p = _project()
    ok, errs = check_manifest(p)
    if a.json:
        print(json.dumps({"ok": ok, "errors": errs}, indent=2))
    else:
        print("PASS datasheets manifest" if ok else f"FAIL datasheets manifest ({len(errs)} error(s))")
        for e in errs:
            print(f"  - {e}")
    return 0 if ok else 1


def add_parser(sub) -> None:
    s = sub.add_parser("datasheets", help="manage datasheets/manifest.yaml and fetch/verify PDFs without committing blobs")
    ds = s.add_subparsers(dest="datasheets_cmd", required=True)

    m = ds.add_parser("manifest", help="generate or update datasheets/manifest.yaml from local PDFs and constraints")
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("--json", action="store_true")
    m.set_defaults(fn=cmd_manifest)

    f = ds.add_parser("fetch", help="download missing datasheets in datasheets/manifest.yaml and verify SHA-256 digests")
    f.add_argument("--verify", action="store_true", help="verify existing PDFs / manifest hashes without downloading")
    f.add_argument("--out", "--dest", dest="out", help="output root directory (default: project root)")
    f.add_argument("--json", action="store_true")
    f.set_defaults(fn=cmd_fetch)

    c = ds.add_parser("check", help="validate datasheets/manifest.yaml and verify SHA-256 of any present PDFs")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_check_ds)
