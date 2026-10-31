"""Vetted import of KiCad symbols/footprints the pinned libraries lack (`ohmai kicad-libs import|fetch|verify|list`).

A missing library part is a tool problem the agent solves with this command, never owner homework.
Sources are vetted only: a manufacturer or Ultra Librarian (UL) KiCad export, or a file the agent has
checked against the datasheet. The tool never draws or guesses a symbol, pin map or land pattern.

Layout (project-local, ${KIPRJMOD} = hardware/):
    hardware/lib/<lib>.kicad_sym     symbols, one library file (default lib name OhmAI_Project)
    hardware/lib/<lib>.pretty/       footprints
    hardware/lib/<lib>.3dshapes/     3D models from the export, if any
    hardware/lib/manifest.yaml       provenance per MPN (source, sha256, date, status, verification)
Table rows `${KIPRJMOD}/lib/...` are added to hardware/sym-lib-table / fp-lib-table (backed up first);
`ohmai kicad-libs tables` keeps them. `ohmai check lib-refs` is the gate.

Fetch is agent-only (LCSC/EasyEDA via easyeda2kicad, or a direct --url); walled sources raise
FetchBlocked (exit 75, retry another source). No owner steps.
Statuses: imported (vetted file in place, no datasheet check), verified (pin table matched the datasheet PDF),
provisional (stopgap source such as a datasheet-transcribed generator; must be replaced by an import).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import yaml

from ohmai.project import Project

DEFAULT_LIB = "OhmAI_Project"
STATUSES = ("imported", "verified", "provisional")
EX_RETRY = 75  # EX_TEMPFAIL: agent-retryable (bot wall, network); try another --source

# Interactive vendor portals (terms/captcha walls). `fetch` probes them and fails agent-retryable; it never
# hands the download to the owner. {mpn}/{base}/{package}/{pins} are filled in.
VENDORS = {
    "ti": ("Texas Instruments -> Ultra Librarian",
           "https://www.ti.com/product/{base}#design-development (CAD/CAE symbols) or "
           "https://vendor.ultralibrarian.com/TI/embedded/?gpn={base}&package={package}&pin={pins}"),
    "ul": ("Ultra Librarian", "https://app.ultralibrarian.com/search?queryText={mpn}"),
    "analog": ("Analog Devices", "https://www.analog.com/en/products/{base}.html (Design Resources -> Symbols and Footprints)"),
    "st": ("STMicroelectronics", "https://www.st.com/en/search.html#q={mpn} (CAD Resources -> ECAD models)"),
    "microchip": ("Microchip", "https://www.microchip.com/en-us/product/{base} (Documentation -> CAD Data)"),
    "nxp": ("NXP", "https://www.nxp.com/search?keyword={mpn} (Design Files -> Symbols and Footprints)"),
    "snapeda": ("SnapEDA (manufacturer-verified parts only)", "https://www.snapeda.com/search/?q={mpn}"),
}


# --------------------------------------------------------------------------- s-expression helpers
def _blocks(text: str, head: str) -> list[tuple[str, str]]:
    """Top-level-of-library `(head "NAME" ...)` blocks as (name, text). String-aware paren matching."""
    out = []
    depth, i, n = 0, 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            depth += 1
            if depth == 2:
                m = re.match(r'\(' + head + r'\s+"([^"]+)"', text[i:])
                if m:
                    start, d, j = i, 0, i
                    while j < n:
                        if text[j] == '"':
                            j += 1
                            while j < n and text[j] != '"':
                                j += 2 if text[j] == "\\" else 1
                        elif text[j] == "(":
                            d += 1
                        elif text[j] == ")":
                            d -= 1
                            if d == 0:
                                break
                        j += 1
                    out.append((m.group(1), text[start:j + 1]))
                    i, depth = j, 1
        elif c == ")":
            depth -= 1
        i += 1
    return out


def symbols_in(text: str) -> dict[str, str]:
    return dict(_blocks(text, "symbol"))


_PIN = re.compile(r'\(pin\s+(\w+)\s+\w+.*?\(name\s+"([^"]*)".*?\(number\s+"([^"]*)"', re.S)


def pins(symbol_text: str) -> list[tuple[str, str, str]]:
    """(number, name, electrical type) for every pin of a symbol block."""
    return [(num, name, et) for et, name, num in _PIN.findall(symbol_text)]


def _rename(symbol_text: str, old: str, new: str) -> str:
    if old == new:
        return symbol_text
    t = re.sub(r'^\(symbol\s+"' + re.escape(old) + '"', f'(symbol "{new}"', symbol_text, count=1)
    return re.sub(r'\(symbol\s+"' + re.escape(old) + r'_(\d+)_(\d+)"', lambda m: f'(symbol "{new}_{m[1]}_{m[2]}"', t)


def _set_prop(symbol_text: str, key: str, value: str) -> str:
    pat = re.compile(r'(\(property\s+"' + re.escape(key) + r'"\s+)"[^"]*"')
    if pat.search(symbol_text):
        return pat.sub(lambda m: m.group(1) + '"' + value.replace('"', "'") + '"', symbol_text, count=1)
    return symbol_text  # never invent a property layout; footprint field is then set in the sheet


# --------------------------------------------------------------------------- project paths / tables
def lib_dir(project: Project) -> Path:
    return project.schematic.parent / "lib"


def manifest_path(project: Project) -> Path:
    return lib_dir(project) / "manifest.yaml"


def load_manifest(project: Project) -> dict:
    f = manifest_path(project)
    data = yaml.safe_load(f.read_text(encoding="utf-8")) if f.is_file() else None
    return data if isinstance(data, dict) and isinstance(data.get("parts"), dict) else {"version": 1, "parts": {}}


def save_manifest(project: Project, data: dict) -> Path:
    f = manifest_path(project)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# Provenance of project-local KiCad parts. Written by `ohmai kicad-libs import`; do not hand-edit.\n"
                 + yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return f


def _backup(f: Path) -> None:
    if f.is_file():
        stamp = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copy2(f, f.with_name(f"{f.name}.bak-{stamp}"))


def ensure_row(table: Path, kind: str, lib: str, uri: str, descr: str) -> bool:
    """Add a `${KIPRJMOD}` row for lib to a sym/fp-lib-table. Returns True when the table changed."""
    head = "sym_lib_table" if kind == "sym" else "fp_lib_table"
    txt = table.read_text(encoding="utf-8") if table.is_file() else f"({head}\n  (version 7)\n)\n"
    if re.search(r'\(name\s+"?' + re.escape(lib) + r'"?\)', txt):
        return False
    row = f'  (lib (name "{lib}")(type "KiCad")(uri "{uri}")(options "")(descr "{descr}"))\n'
    k = txt.rstrip().rfind(")")
    _backup(table)
    table.write_text(txt[:k].rstrip("\n") + "\n" + row + ")\n", encoding="utf-8")
    return True


# --------------------------------------------------------------------------- sources
def _rel(project: Project, f: Path) -> str:
    try:
        return str(f.resolve().relative_to(project.root.resolve()))
    except ValueError:
        return str(f)


_MODEL = re.compile(r'\(model\s+"([^"]+)"')


def fix_model_paths(text: str, lib: str) -> str:
    """Point every footprint (model ...) at ${KIPRJMOD}/lib/<lib>.3dshapes/<file>; never an absolute/temp path.
    Paths already using ${KICAD*_3DMODEL_DIR} (stock models) are kept."""
    def sub(m):
        path = m.group(1)
        if path.startswith("${KICAD"):
            return m.group(0)
        name = re.split(r"[\\/]", path)[-1]
        return f'(model "${{KIPRJMOD}}/lib/{lib}.3dshapes/{name}"'
    return _MODEL.sub(sub, text)


def _sha(f: Path) -> str:
    return hashlib.sha256(f.read_bytes()).hexdigest()


def collect(source: Path, work: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """(.kicad_sym, .kicad_mod, 3D) files in a file, a directory or a UL/manufacturer zip."""
    if source.is_file() and source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as z:
            for m in z.infolist():
                if m.is_dir() or ".." in Path(m.filename).parts or m.filename.startswith("/"):
                    continue
                z.extract(m, work)
        root = work
    elif source.is_dir():
        root = source
    elif source.is_file():
        root = source.parent
        sel = [source]
        return ([f for f in sel if f.suffix == ".kicad_sym"], [f for f in sel if f.suffix == ".kicad_mod"], [])
    else:
        raise SystemExit(f"ohmai kicad-libs import: {source} not found")
    files = sorted(p for p in root.rglob("*") if p.is_file())
    # UL zips carry several CAD formats; KiCad v6+ files are .kicad_sym / .kicad_mod (legacy .lib/.mod refused)
    return ([f for f in files if f.suffix == ".kicad_sym"], [f for f in files if f.suffix == ".kicad_mod"],
            [f for f in files if f.suffix.lower() in (".step", ".stp", ".wrl")])


# --------------------------------------------------------------------------- datasheet check
def datasheet_text(pdf: Path, pages: str | None = None) -> str:
    cmd = ["pdftotext", "-layout"]
    if pages:
        a, _, b = pages.partition("-")
        cmd += ["-f", a, "-l", b or a]
    try:
        return subprocess.run(cmd + [str(pdf), "-"], capture_output=True, text=True, check=True).stdout
    except FileNotFoundError as e:
        raise SystemExit("ohmai kicad-libs verify: pdftotext (poppler-utils) not installed") from e


def _clean(name: str) -> str:
    return re.sub(r"~\{([^}]*)\}", r"\1", name).replace("~", "").strip()


def verify_pins(symbol_pins: list[tuple[str, str, str]], text: str) -> list[str]:
    """Each (number, name) must appear together on one line of the datasheet pin table text.

    Generic and conservative: it reports pins it cannot find; it never edits the symbol. Pins with
    empty names are skipped; a PAD/EP pin passes when the datasheet has a thermal/exposed pad row.
    """
    lines = text.splitlines()
    has_pad = re.search(r"(thermal|exposed|power)\s*pad", text, re.I) is not None
    errs = []
    seen_num: dict[str, str] = {}
    for num, name, _ in symbol_pins:
        if num in seen_num and seen_num[num] != name:
            continue  # stacked pins sharing a number (e.g. several GND names) -> checked once
        seen_num[num] = name
        n = _clean(name)
        if not n or n in ("~", num):
            continue
        if has_pad and n.upper() in ("PAD", "EP", "EPAD", "TP", "THERMAL_PAD", "EXPOSED_PAD"):
            continue  # exposed/thermal pad: datasheets list it without a number
        tok_name = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(n) + r"(?![A-Za-z0-9_])", re.I)
        tok_num = re.compile(r"(?<![A-Za-z0-9.])" + re.escape(num) + r"(?![A-Za-z0-9.])")
        if not any(tok_name.search(ln) and tok_num.search(tok_name.sub(" ", ln)) for ln in lines):
            errs.append(f"pin {num} {name}: no datasheet line pairs this name and number")
    return errs


IO_TYPES = {"I": "input", "O": "output", "I/O": "bidirectional", "IO": "bidirectional", "OD": "open_collector",
            "PWR": "passive", "P": "passive", "G": "passive", "GND": "passive", "A": "passive", "NC": "no_connect"}


def datasheet_pin_types(symbol_text: str, text: str) -> tuple[str, list[str]]:
    """Fill `unspecified` pin electrical types from the datasheet I/O column (token right after the pin number).

    Only unspecified pins change; power/ground/analog map to passive (no invented drivers). Returns the new
    symbol text and a change log. Pins whose line or I/O token is not found stay unspecified.
    """
    lines = text.splitlines()
    has_pad = re.search(r"(thermal|exposed|power)\s*pad", text, re.I) is not None
    log = []

    def sub(m: re.Match) -> str:
        whole, et, name, num = m.group(0), m.group(1), m.group(2), m.group(3)
        if et != "unspecified":
            return whole
        n = _clean(name)
        if has_pad and n.upper() in ("PAD", "EP", "EPAD", "TP", "THERMAL_PAD", "EXPOSED_PAD"):
            log.append(f"pin {num} {name}: unspecified -> passive (datasheet thermal/exposed pad row)")
            return whole.replace("(pin unspecified", "(pin passive", 1)
        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(n) + r"\s+" + re.escape(num) +
                         r"\s+(I/O|IO|OD|PWR|GND|NC|I|O|P|G|A)\b", re.I)
        for ln in lines:
            k = pat.search(ln)
            if k:
                new = IO_TYPES[k.group(1).upper()]
                log.append(f"pin {num} {name}: unspecified -> {new} (datasheet I/O '{k.group(1)}')")
                return whole.replace("(pin unspecified", f"(pin {new}", 1)
        return whole

    out = re.sub(r'\(pin\s+(\w+)\s+\w+.*?\(name\s+"([^"]*)".*?\(number\s+"([^"]*)"', sub, symbol_text, flags=re.S)
    return out, log


# --------------------------------------------------------------------------- commands
def import_part(project: Project, mpn: str, source: Path, *, package: str | None = None, source_url: str | None = None,
                lib: str = DEFAULT_LIB, symbol: str | None = None, name: str | None = None,
                footprint: str | None = None, datasheet: Path | None = None, pages: str | None = None,
                status: str | None = None, replace: bool = False, note: str | None = None,
                pin_types: bool = False) -> dict:
    """Import a vetted symbol and/or footprint into the project library. Returns the manifest entry."""
    if not (source_url or status == "provisional"):
        raise SystemExit("ohmai kicad-libs import: --source-url is required (where the vetted file came from)")
    ld = lib_dir(project)
    ld.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        syms, mods, models = collect(source, Path(td))
        if not syms and not mods:
            raise SystemExit(f"ohmai kicad-libs import: no .kicad_sym or .kicad_mod in {source} "
                             "(pick the KiCad v6+ export; legacy .lib/.mod are not imported)")
        entry: dict = {"mpn": mpn, "package": package, "source_url": source_url, "source_file": source.name,
                       "source_sha256": _sha(source) if source.is_file() else None,
                       "imported": _dt.date.today().isoformat(), "lib": lib}
        if note:
            entry["note"] = note
        # ---- symbol
        sym_entry = None
        if syms:
            found: dict[str, str] = {}
            for f in syms:
                found.update(symbols_in(f.read_text(encoding="utf-8", errors="replace")))
            if symbol:
                if symbol not in found:
                    raise SystemExit(f"ohmai kicad-libs import: symbol {symbol!r} not in source ({', '.join(found)})")
                pick = symbol
            elif len(found) == 1:
                pick = next(iter(found))
            else:
                raise SystemExit(f"ohmai kicad-libs import: several symbols in source, choose one with --symbol: "
                                 f"{', '.join(found)}")
            new = name or pick
            block = _rename(found[pick], pick, new)
            type_log: list[str] = []
            if pin_types and datasheet:
                block, type_log = datasheet_pin_types(block, datasheet_text(datasheet, pages))
            target = ld / f"{lib}.kicad_sym"
            txt = target.read_text(encoding="utf-8") if target.is_file() else \
                '(kicad_symbol_lib (version 20241209) (generator "ohmai_libimport") (generator_version "1")\n)\n'
            existing = symbols_in(txt)
            if new in existing:
                if not replace:
                    raise SystemExit(f"ohmai kicad-libs import: {lib}:{new} already exists (use --replace to swap "
                                     "in the vetted file; the old one is backed up)")
                _backup(target)
                txt = txt.replace(existing[new], "").replace("\n\n\n", "\n")
            k = txt.rstrip().rfind(")")
            txt = txt[:k].rstrip("\n") + "\n  " + block.strip() + "\n)\n"
            target.write_text(txt, encoding="utf-8")
            ps = pins(block)
            sym_entry = {"lib_id": f"{lib}:{new}", "source_symbol": pick, "pins": len(ps)}
            if type_log:
                sym_entry["pin_types_from_datasheet"] = type_log
            unspec = sum(1 for p in ps if p[2] == "unspecified")
            if unspec:
                entry.setdefault("warnings", []).append(
                    f"{unspec} of {len(ps)} pins have electrical type 'unspecified' (ERC cannot check them; "
                    "re-import with --pin-types)")
            entry["symbol"] = sym_entry
            ensure_row(project.schematic.parent / "sym-lib-table", "sym", lib,
                       f"${{KIPRJMOD}}/lib/{lib}.kicad_sym", "Project-local vetted symbols (lib/manifest.yaml)")
        # ---- footprint(s)
        if mods:
            pretty = ld / f"{lib}.pretty"
            pretty.mkdir(exist_ok=True)
            names = []
            for f in mods:
                dst = pretty / f.name
                if dst.exists() and not replace and dst.read_text(encoding="utf-8") != fix_model_paths(f.read_text(encoding="utf-8"), lib):
                    raise SystemExit(f"ohmai kicad-libs import: {dst.name} exists and differs (use --replace)")
                if dst.exists():
                    _backup(dst)
                dst.write_text(fix_model_paths(f.read_text(encoding="utf-8"), lib), encoding="utf-8")
                names.append(f"{lib}:{f.stem}")
            entry["footprints"] = names
            ensure_row(project.schematic.parent / "fp-lib-table", "fp", lib,
                       f"${{KIPRJMOD}}/lib/{lib}.pretty", "Project-local vetted footprints (lib/manifest.yaml)")
            if sym_entry and footprint is None and len(names) == 1:
                footprint = names[0]
        if footprint and sym_entry:
            target = ld / f"{lib}.kicad_sym"
            t = target.read_text(encoding="utf-8")
            blk = symbols_in(t)[sym_entry["lib_id"].split(":", 1)[1]]
            target.write_text(t.replace(blk, _set_prop(blk, "Footprint", footprint)), encoding="utf-8")
            sym_entry["footprint"] = footprint
        if models:
            shapes = ld / f"{lib}.3dshapes"
            shapes.mkdir(exist_ok=True)
            for f in models:
                shutil.copy2(f, shapes / f.name)
            entry["models"] = [f.name for f in models]
    # ---- datasheet verification
    st = status or "imported"
    if datasheet and sym_entry:
        t = (ld / f"{lib}.kicad_sym").read_text(encoding="utf-8")
        errs = verify_pins(pins(symbols_in(t)[sym_entry["lib_id"].split(":", 1)[1]]), datasheet_text(datasheet, pages))
        entry["verification"] = {"datasheet": _rel(project, datasheet), "datasheet_sha256": _sha(datasheet), "pages": pages,
                                 "date": _dt.date.today().isoformat(), "mismatches": errs}
        if not errs and st == "imported":
            st = "verified"
    entry["status"] = st
    data = load_manifest(project)
    data["parts"][mpn] = entry
    save_manifest(project, data)
    return entry


def verify_part(project: Project, mpn: str, datasheet: Path, pages: str | None = None) -> list[str]:
    data = load_manifest(project)
    e = data["parts"].get(mpn)
    if not e or not e.get("symbol"):
        raise SystemExit(f"ohmai kicad-libs verify: {mpn} has no imported symbol in {manifest_path(project)}")
    lib, nm = e["symbol"]["lib_id"].split(":", 1)
    syms = symbols_in((lib_dir(project) / f"{lib}.kicad_sym").read_text(encoding="utf-8"))
    if nm not in syms:
        raise SystemExit(f"ohmai kicad-libs verify: {e['symbol']['lib_id']} missing from the library file")
    errs = verify_pins(pins(syms[nm]), datasheet_text(datasheet, pages))
    e["verification"] = {"datasheet": _rel(project, datasheet), "datasheet_sha256": _sha(datasheet), "pages": pages,
                         "date": _dt.date.today().isoformat(), "mismatches": errs}
    if e.get("status") == "imported" and not errs:
        e["status"] = "verified"
    save_manifest(project, data)
    return errs


class FetchBlocked(SystemExit):
    """Agent-retryable fetch failure (bot wall, captcha page, network). Exit code 75. Never owner homework."""

    def __init__(self, msg: str):
        super().__init__(EX_RETRY)
        self.msg = msg

    def __str__(self) -> str:
        return self.msg


EASYEDA_API = "https://easyeda.com/api/products/{lcsc}/components?version=6.4.19.5"
UA = "Mozilla/5.0 (X11; Linux x86_64) OhmAI-kicad-libs"


def _http(url: str, timeout: int = 30) -> tuple[bytes, str]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (https URLs from vendors/args)
            return r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        raise FetchBlocked(f"{url}: HTTP {e.code} (bot wall or login). Retry with --source easyeda / --url") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchBlocked(f"{url}: {e} (network; retry later or another --source)") from e


def lcsc_for(project: Project, mpn: str) -> str | None:
    """LCSC number for mpn from bom/bom.csv (supplier LCSC, supplier_pn C...)."""
    import csv

    f = project.root / "bom" / "bom.csv"
    if not f.is_file():
        return None
    for row in csv.DictReader(f.open(encoding="utf-8")):
        if (row.get("mpn") or "").strip().upper() == mpn.upper():
            pn = (row.get("supplier_pn") or "").strip()
            if re.fullmatch(r"C\d+", pn):
                return pn
    return None


def find_datasheet(project: Project, mpn: str, lcsc: str | None) -> Path | None:
    d = project.root / "datasheets"
    if not d.is_dir():
        return None
    for pdf in sorted(d.glob("*.pdf")):
        n = pdf.name.upper()
        if mpn.upper() in n or (lcsc and f"_{lcsc.upper()}" in n):
            return pdf
    return None


def easyeda_meta(lcsc: str) -> dict:
    import json

    body, _ = _http(EASYEDA_API.format(lcsc=lcsc))
    try:
        data = json.loads(body)
    except ValueError as e:
        raise FetchBlocked(f"EasyEDA API for {lcsc}: not JSON (bot wall?)") from e
    if not data.get("success") or not data.get("result"):
        raise SystemExit(f"ohmai kicad-libs fetch: EasyEDA has no CAD data for {lcsc}")
    return data["result"]


def _easyeda2kicad() -> list[str]:
    exe = shutil.which("easyeda2kicad")
    if exe:
        return [exe]
    import importlib.util
    import sys

    if importlib.util.find_spec("easyeda2kicad"):
        return [sys.executable, "-m", "easyeda2kicad"]
    raise SystemExit("ohmai kicad-libs fetch: easyeda2kicad not installed (pip install easyeda2kicad; MIT, "
                     "run as a subprocess)")


def upgrade_footprints(pretty: Path) -> list[str]:
    """Convert legacy (module ...) footprints to KiCad 9 format with `kicad-cli fp upgrade` when available."""
    legacy = [f for f in pretty.glob("*.kicad_mod") if f.read_text(errors="replace").lstrip().startswith("(module")]
    if not legacy:
        return []
    cli = shutil.which("kicad-cli")
    if not cli:
        return [f"{f.name}: legacy KiCad 5 format kept (kicad-cli not found for `fp upgrade`)" for f in legacy]
    subprocess.run([cli, "fp", "upgrade", "--force", str(pretty)], capture_output=True, text=True, check=False)
    return [f"{f.name}: legacy KiCad 5 format left after kicad-cli fp upgrade" for f in legacy
            if f.read_text(errors="replace").lstrip().startswith("(module")]


def footprint_summary(mod: Path) -> dict:
    t = mod.read_text(errors="replace")
    pads = re.findall(r'\(pad\s+"?([^"\s)]+)"?\s+\w+\s+\w+.*?\(size\s+([\d.]+)\s+([\d.]+)\)', t, re.S)
    nums = sorted({p[0] for p in pads if p[0]}, key=lambda x: (len(x), x))
    big = max(pads, key=lambda p: float(p[1]) * float(p[2]), default=None)
    return {"file": mod.name, "pads": len(pads), "pad_numbers": len(nums),
            "largest_pad_mm": f"{float(big[1]):g}x{float(big[2]):g}" if big else None}


def fetch_easyeda(project: Project, mpn: str, lcsc: str | None = None, **kw) -> dict:
    """Agent-only fetch: LCSC/EasyEDA CAD via easyeda2kicad (no login, no captcha), then import + verify."""
    lcsc = lcsc or lcsc_for(project, mpn)
    if not lcsc:
        raise SystemExit(f"ohmai kicad-libs fetch: no LCSC number for {mpn} (pass --lcsc C123 or set supplier_pn "
                         "in bom/bom.csv; `ohmai parts search` finds it)")
    meta = easyeda_meta(lcsc)
    title = str(meta.get("title") or "")
    if title.upper().replace("-", "") != mpn.upper().replace("-", "") and not kw.pop("allow_title_mismatch", False):
        raise SystemExit(f"ohmai kicad-libs fetch: EasyEDA {lcsc} is {title!r}, not {mpn} (check the LCSC number)")
    kw.pop("allow_title_mismatch", None)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "ee"
        r = subprocess.run(_easyeda2kicad() + ["--full", f"--lcsc_id={lcsc}", "--output", str(out)],
                           capture_output=True, text=True, check=False)
        pretty = out.with_suffix(".pretty")
        if r.returncode != 0 or not (out.with_suffix(".kicad_sym").is_file() or pretty.is_dir()):
            raise FetchBlocked(f"easyeda2kicad {lcsc} failed: {(r.stderr or r.stdout).strip()[-400:]}")
        notes = upgrade_footprints(pretty) if pretty.is_dir() else []
        fps = [footprint_summary(f) for f in sorted(pretty.glob("*.kicad_mod"))] if pretty.is_dir() else []
        ds = kw.pop("datasheet", None) or find_datasheet(project, mpn, lcsc)
        e = import_part(project, mpn, Path(td), source_url=EASYEDA_API.format(lcsc=lcsc), datasheet=ds, **kw)
    e["source"] = {"kind": "easyeda", "lcsc": lcsc, "title": title, "tool": "easyeda2kicad"}
    e["footprint_check"] = fps
    if notes:
        e["warnings"] = (e.get("warnings") or []) + notes
    data = load_manifest(project)
    data["parts"][mpn] = e
    save_manifest(project, data)
    return e


def fetch_url(project: Project, mpn: str, url: str, **kw) -> dict:
    """Agent-only fetch of a direct manufacturer / mirror file (.zip, .kicad_sym, .kicad_mod)."""
    body, ctype = _http(url)
    if "html" in ctype.lower() or body[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
        raise FetchBlocked(f"{url}: got an HTML page, not a CAD file (login/captcha wall). "
                           "Retry with --source easyeda or a direct file URL")
    name = Path(url.split("?")[0]).name or "download.zip"
    if not name.endswith((".zip", ".kicad_sym", ".kicad_mod")):
        name += ".zip" if body[:2] == b"PK" else ".kicad_sym"
    dl = lib_dir(project) / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    f = dl / name
    f.write_bytes(body)
    if kw.get("datasheet") is None:
        kw["datasheet"] = find_datasheet(project, mpn, None)
    return import_part(project, mpn, f, source_url=url, **kw)


def probe_portal(vendor: str, mpn: str, base: str | None, package: str | None, pins_count: str | None) -> None:
    """UL/SnapEDA/vendor portals: try the page; a wall raises FetchBlocked (retryable), never owner homework."""
    who, url = VENDORS[vendor]
    u = url.format(mpn=mpn, base=base or mpn, package=package or "", pins=pins_count or "").split(" ")[0]
    body, ctype = _http(u)
    if "html" in ctype.lower():
        raise FetchBlocked(f"{who} ({u}) serves an interactive page (terms/captcha), no agent download. "
                           "Use --source easyeda (LCSC) or --url <direct CAD file>")
    raise FetchBlocked(f"{who}: unexpected {ctype} response; use --url with the direct file")


def fetch(project: Project, mpn: str, *, source: str = "auto", lcsc: str | None = None, url: str | None = None,
          vendor: str | None = None, base: str | None = None, pins_count: str | None = None, **kw) -> dict:
    """Agent end-to-end: download a vetted KiCad part and import + verify it. source: auto|easyeda|url|portal.

    auto = --url if given, else EasyEDA/LCSC (bom.csv supplier_pn or --lcsc). Blocked sources raise
    FetchBlocked (exit 75) so the agent retries another source; no step is handed to the owner.
    """
    if source == "url" or (source == "auto" and url):
        if not url:
            raise SystemExit("ohmai kicad-libs fetch --source url: --url required")
        return fetch_url(project, mpn, url, **kw)
    if source in ("auto", "easyeda"):
        return fetch_easyeda(project, mpn, lcsc, **kw)
    probe_portal(vendor or "ul", mpn, base, kw.get("package"), pins_count)
    raise AssertionError("unreachable")
