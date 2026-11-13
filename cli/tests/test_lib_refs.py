# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""lib-refs: schematic/BOM references must resolve through the project lib tables (missing lib = tool issue)."""

from __future__ import annotations

from ohmai import kicadlibs
from ohmai.checks import FAIL, PASS, SKIP, WARN, run
from ohmai.cli import main
from ohmai.phases import BY_ID
from ohmai.project import find_project

SYM = '(kicad_symbol_lib (version 20241209)\n  (symbol "CHIP"\n    (symbol "CHIP_1_1"))\n)\n'
SCH = ('(kicad_sch (version 20250114)\n  (lib_symbols\n    (symbol "Gone:CACHED")\n  )\n'
       '  (symbol (lib_id "{lib}:CHIP") (at 0 0 0)\n    (property "Footprint" "{fp}"))\n)\n')


def _proj(tmp_path, monkeypatch):
    d = tmp_path / "p"
    assert main(["new", str(d), "--idea", "x", "--no-git"]) == 0
    monkeypatch.chdir(d)
    monkeypatch.delenv(kicadlibs.TAG_ENV, raising=False)
    p = find_project(d)
    p.config.pop("kicad", None)  # no pinned tag: tables are taken as written
    hw = p.schematic.parent
    (hw / "lib" / "Local.pretty").mkdir(parents=True)
    (hw / "lib" / "Local.kicad_sym").write_text(SYM, encoding="utf-8")
    (hw / "lib" / "Local.pretty" / "PAD.kicad_mod").write_text("(footprint \"PAD\")\n", encoding="utf-8")
    (hw / "sym-lib-table").write_text('(sym_lib_table\n  (version 7)\n  (lib (name "Local")(type "KiCad")'
                                      '(uri "${KIPRJMOD}/lib/Local.kicad_sym")(options "")(descr ""))\n)\n')
    (hw / "fp-lib-table").write_text('(fp_lib_table\n  (version 7)\n  (lib (name "Local")(type "KiCad")'
                                     '(uri "${KIPRJMOD}/lib/Local.pretty")(options "")(descr ""))\n)\n')
    (p.root / "bom" / "bom.csv").write_text("refs,footprint\n", encoding="utf-8")
    if p.schematic.exists():
        p.schematic.unlink()
    return p


def _sch(p, lib="Local", fp="Local:PAD"):
    p.schematic.write_text(SCH.format(lib=lib, fp=fp), encoding="utf-8")


def test_skip_without_refs(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    assert run(p, "lib-refs", {}).status == SKIP


def test_resolves_and_ignores_embedded_cache(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    _sch(p)
    r = run(p, "lib-refs", {})
    assert r.status == PASS, r.details


def test_missing_symbol_fails(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    _sch(p, lib="Nope")
    r = run(p, "lib-refs", {})
    assert r.status == FAIL and "Nope" in r.details[0]
    (p.schematic.parent / "lib" / "Local.kicad_sym").write_text(SYM.replace("CHIP", "OTHER"), encoding="utf-8")
    _sch(p)
    assert run(p, "lib-refs", {}).status == FAIL


def test_missing_footprint_warns_then_strict_fails(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    _sch(p)
    (p.root / "bom" / "bom.csv").write_text("refs,footprint\nU1,Local:MISSING\nR1,\n", encoding="utf-8")
    assert run(p, "lib-refs", {}).status == WARN
    r = run(p, "lib-refs-strict", {})
    assert r.status == FAIL and "bom.csv U1" in r.details[0]


def test_write_tables_keeps_project_local_rows(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    base = tmp_path / "libs"
    monkeypatch.setenv("OHMAI_KICAD_LIBS", str(base))
    sym, fp = kicadlibs.dirs("9.9")
    sym.mkdir(parents=True)
    fp.mkdir(parents=True)
    (sym / "Device.kicad_sym").write_text(SYM, encoding="utf-8")
    (fp / "Resistor_SMD.pretty").mkdir()
    p.config["kicad"] = {"libraries": "9.9"}
    st, ft = kicadlibs.write_tables(p)
    assert '"Device"' in st.read_text() and "${KIPRJMOD}/lib/Local.kicad_sym" in st.read_text()
    assert "${KIPRJMOD}/lib/Local.pretty" in ft.read_text()


def test_phase_wiring():
    assert "lib-refs" in BY_ID["schematic"].checks
    assert "lib-refs-strict" in BY_ID["layout"].checks and "lib-refs-strict" in BY_ID["bom"].checks
