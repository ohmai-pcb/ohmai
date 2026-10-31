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


def test_fetch_records_pending_and_prints_human_steps(tmp_path, monkeypatch, capsys):
    p = _proj(tmp_path, monkeypatch)
    assert main(["kicad-libs", "fetch", "TPS63001RSNR", "--vendor", "ti", "--base", "TPS63001",
                 "--package", "RSN", "--pins", "32"]) == 0
    out = capsys.readouterr().out
    assert "gpn=TPS63001&package=RSN&pin=32" in out and "captcha" in out
    e = libimport.load_manifest(p)["parts"]["TPS63001RSNR"]
    assert e["status"] == "pending-download" and e["fetch"]["needs_human"]
    assert main(["kicad-libs", "list"]) == 0
