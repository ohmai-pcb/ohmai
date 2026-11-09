# SPDX-License-Identifier: Apache-2.0
"""kicad-libs import/fetch/verify/list: vetted project-local parts, tables, manifest, lib-refs integration."""

from __future__ import annotations

import shutil
import zipfile

import pytest
import yaml

from ohmai import kicadlibs, libimport
from ohmai.checks import PASS, run
from ohmai.cli import main
from ohmai.project import find_project

# Fictional test part (not a real device): two pins.
SYM = ('(kicad_symbol_lib (version 20241209) (generator "test")\n'
       '  (symbol "FAKE1" (in_bom yes)\n'
       '    (property "Reference" "U" (at 0 0 0))\n    (property "Footprint" "" (at 0 0 0))\n'
       '    (symbol "FAKE1_1_1"\n'
       '      (pin input line (at 0 0 0) (length 2.54) (name "EN" (effects)) (number "1" (effects)))\n'
       '      (pin power_in line (at 0 2 0) (length 2.54) (name "GND" (effects)) (number "2" (effects)))\n'
       '    )\n  )\n)\n')
MOD = '(footprint "FAKE_PKG" (layer "F.Cu"))\n'
SCH = '(kicad_sch (version 20250114)\n  (symbol (lib_id "OhmAI_Project:FAKE1") (at 0 0 0)\n    (property "Footprint" "OhmAI_Project:FAKE_PKG"))\n)\n'


def _proj(tmp_path, monkeypatch):
    d = tmp_path / "p"
    assert main(["new", str(d), "--idea", "x", "--no-git"]) == 0
    monkeypatch.chdir(d)
    monkeypatch.delenv(kicadlibs.TAG_ENV, raising=False)
    p = find_project(d)
    hw = p.schematic.parent
    for t, h in (("sym-lib-table", "sym_lib_table"), ("fp-lib-table", "fp_lib_table")):
        (hw / t).write_text(f"({h}\n  (version 7)\n)\n", encoding="utf-8")
    (p.root / "bom" / "bom.csv").write_text("refs,footprint\n", encoding="utf-8")
    return p


def _zip(tmp_path):
    z = tmp_path / "ul_FAKE1.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("KiCADv6/FAKE1.kicad_sym", SYM)
        f.writestr("KiCADv6/footprints.pretty/FAKE_PKG.kicad_mod", MOD)
        f.writestr("Altium/FAKE1.PcbLib", "x")
    return z


def test_import_zip_tables_manifest_and_lib_refs(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    z = _zip(tmp_path)
    assert main(["kicad-libs", "import", "FAKE1X", "--from", str(z), "--source-url", "https://example.invalid/ul",
                 "--package", "SOT"]) == 0
    hw = p.schematic.parent
    assert "FAKE1" in libimport.symbols_in((hw / "lib" / "OhmAI_Project.kicad_sym").read_text())
    assert (hw / "lib" / "OhmAI_Project.pretty" / "FAKE_PKG.kicad_mod").is_file()
    assert "${KIPRJMOD}/lib/OhmAI_Project.kicad_sym" in (hw / "sym-lib-table").read_text()
    assert "${KIPRJMOD}/lib/OhmAI_Project.pretty" in (hw / "fp-lib-table").read_text()
    e = yaml.safe_load((hw / "lib" / "manifest.yaml").read_text())["parts"]["FAKE1X"]
    assert e["status"] == "imported" and e["symbol"]["pins"] == 2 and len(e["source_sha256"]) == 64
    assert e["symbol"]["footprint"] == "OhmAI_Project:FAKE_PKG"
    assert '"Footprint" "OhmAI_Project:FAKE_PKG"' in (hw / "lib" / "OhmAI_Project.kicad_sym").read_text()
    # kicad-libs tables keeps the rows; lib-refs resolves the imported part
    assert kicadlibs.local_rows(hw / "sym-lib-table")
    p.config.pop("kicad", None)
    p.schematic.write_text(SCH, encoding="utf-8")
    r = run(p, "lib-refs", {})
    assert r.status == PASS, r.details


def test_import_requires_source_url_and_refuses_silent_overwrite(tmp_path, monkeypatch):
    _proj(tmp_path, monkeypatch)
    z = _zip(tmp_path)
    with pytest.raises(SystemExit, match="source-url"):
        main(["kicad-libs", "import", "FAKE1X", "--from", str(z)])
    assert main(["kicad-libs", "import", "FAKE1X", "--from", str(z), "--source-url", "u"]) == 0
    with pytest.raises(SystemExit, match="already exists"):
        main(["kicad-libs", "import", "FAKE1X", "--from", str(z), "--source-url", "u"])
    assert main(["kicad-libs", "import", "FAKE1X", "--from", str(z), "--source-url", "u", "--replace"]) == 0


def test_import_renames_and_keeps_other_symbols(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    sym = tmp_path / "a.kicad_sym"
    sym.write_text(SYM)
    assert main(["kicad-libs", "import", "A", "--from", str(sym), "--source-url", "u", "--name", "FAKE_A"]) == 0
    assert main(["kicad-libs", "import", "B", "--from", str(sym), "--source-url", "u", "--name", "FAKE_B"]) == 0
    s = libimport.symbols_in((p.schematic.parent / "lib" / "OhmAI_Project.kicad_sym").read_text())
    assert set(s) == {"FAKE_A", "FAKE_B"} and '"FAKE_B_1_1"' in s["FAKE_B"]


def test_verify_pins_against_datasheet_text():
    ps = libimport.pins(libimport.symbols_in(SYM)["FAKE1"])
    assert ps == [("1", "EN", "input"), ("2", "GND", "power_in")]
    good = "PIN\nNAME   NO.  I/O\nEN     1    I   Enable\nGND    2    -   Ground\n"
    assert libimport.verify_pins(ps, good) == []
    bad = "EN     2    I\nGND    1    -\n"
    assert len(libimport.verify_pins(ps, bad)) == 2
    pad = [("33", "PAD", "power_in")]
    assert libimport.verify_pins(pad, "Thermal pad  -  connect to ground\n") == []
    assert libimport.verify_pins(pad, "no such row\n")


@pytest.mark.skipif(not shutil.which("pdftotext"), reason="pdftotext not installed")
def test_verify_cli_marks_verified(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    sym = tmp_path / "a.kicad_sym"
    sym.write_text(SYM)
    assert main(["kicad-libs", "import", "FAKE1X", "--from", str(sym), "--source-url", "u"]) == 0
    monkeypatch.setattr(libimport, "datasheet_text", lambda pdf, pages=None: "EN 1 I\nGND 2 G\n")
    pdf = tmp_path / "ds.pdf"
    pdf.write_bytes(b"%PDF-fake")
    assert main(["kicad-libs", "verify", "FAKE1X", "--datasheet", str(pdf)]) == 0
    assert libimport.load_manifest(p)["parts"]["FAKE1X"]["status"] == "verified"


def _fake_easyeda(monkeypatch, title="FAKE1X"):
    monkeypatch.setattr(libimport, "easyeda_meta", lambda lcsc: {"title": title})

    def fake_run(cmd, **kw):
        out = __import__("pathlib").Path(cmd[cmd.index("--output") + 1])
        sym = SYM.replace("(pin input", "(pin unspecified").replace("(pin power_in", "(pin unspecified")
        out.with_suffix(".kicad_sym").write_text(sym)
        out.with_suffix(".pretty").mkdir()
        out.with_suffix(".pretty").joinpath("FAKE_PKG.kicad_mod").write_text(
            '(footprint "FAKE_PKG"\n  (model "/tmp/x/ee.3dshapes/FAKE.wrl")\n  (pad "1" smd rect (at 0 0) (size 0.2 0.7))\n  (pad "2" smd rect (at 1 0) (size 2.8 2.8))\n)\n')
        return __import__("subprocess").CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(libimport, "_easyeda2kicad", lambda: ["easyeda2kicad"])
    monkeypatch.setattr(libimport.subprocess, "run", fake_run)
    monkeypatch.setattr(libimport, "datasheet_text", lambda pdf, pages=None: "EN 1 I Enable\nGND 2 GND Ground\n")


def test_fetch_easyeda_agent_only_uses_bom_lcsc_verifies_and_types(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    (p.root / "bom" / "bom.csv").write_text("refs,mpn,supplier,supplier_pn\nU1,FAKE1X,LCSC,C999\n", encoding="utf-8")
    (p.root / "datasheets").mkdir(exist_ok=True)
    (p.root / "datasheets" / "FAKE1X_C999.pdf").write_bytes(b"%PDF-fake")
    _fake_easyeda(monkeypatch)
    assert main(["kicad-libs", "fetch", "FAKE1X", "--pin-types"]) == 0
    e = libimport.load_manifest(p)["parts"]["FAKE1X"]
    assert e["status"] == "verified" and e["source"]["lcsc"] == "C999" and "easyeda.com" in e["source_url"]
    assert e["footprint_check"][0]["pads"] == 2 and e["footprint_check"][0]["largest_pad_mm"] == "2.8x2.8"
    t = (p.schematic.parent / "lib" / "OhmAI_Project.kicad_sym").read_text()
    assert "(pin input" in t and "(pin passive" in t and "unspecified" not in t
    fp = (p.schematic.parent / "lib" / "OhmAI_Project.pretty" / "FAKE_PKG.kicad_mod").read_text()
    assert "${KIPRJMOD}/lib/OhmAI_Project.3dshapes/FAKE.wrl" in fp and "/tmp/" not in fp


def test_fetch_easyeda_refuses_wrong_part(tmp_path, monkeypatch):
    _proj(tmp_path, monkeypatch)
    _fake_easyeda(monkeypatch, title="OTHER")
    with pytest.raises(SystemExit, match="not FAKE1X"):
        main(["kicad-libs", "fetch", "FAKE1X", "--lcsc", "C1"])


def test_fetch_without_lcsc_is_a_clear_agent_error(tmp_path, monkeypatch):
    _proj(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="no LCSC number"):
        main(["kicad-libs", "fetch", "NOPE"])


def test_blocked_sources_exit_retryable_never_owner_steps(tmp_path, monkeypatch, capsys):
    _proj(tmp_path, monkeypatch)
    monkeypatch.setattr(libimport, "_http", lambda url, timeout=30: (b"<!doctype html><p>captcha", "text/html"))
    assert main(["kicad-libs", "fetch", "X1", "--url", "https://example.invalid/x.zip"]) == libimport.EX_RETRY
    assert main(["kicad-libs", "fetch", "X1", "--source", "portal", "--vendor", "ti"]) == libimport.EX_RETRY
    err = capsys.readouterr().err
    assert "RETRY" in err and "owner" not in err.lower()


def test_fetch_url_downloads_zip_and_imports(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    body = _zip(tmp_path).read_bytes()
    monkeypatch.setattr(libimport, "_http", lambda url, timeout=30: (body, "application/zip"))
    assert main(["kicad-libs", "fetch", "FAKE1X", "--url", "https://example.invalid/FAKE1.zip"]) == 0
    assert (p.schematic.parent / "lib" / "downloads" / "FAKE1.zip").is_file()
    assert libimport.load_manifest(p)["parts"]["FAKE1X"]["status"] == "imported"
    assert main(["kicad-libs", "list"]) == 0
