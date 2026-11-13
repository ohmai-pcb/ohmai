# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Derived (`extends`) symbols must be embedded the way KiCad's LIB_SYMBOL::Flatten() does it,
otherwise ERC reports lib_symbol_mismatch (seen on Sensor_Temperature:LM75B extends LM75C)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ohmai.schgen import Lib

FONT = "(effects (font (size 1.27 1.27)))"
LIB = f'''(kicad_symbol_lib (version 20241209) (generator "t")
\t(symbol "PAR"
\t\t(property "Reference" "U" (at 0 5 0) {FONT})
\t\t(property "Value" "PAR" (at 0 -5 0) {FONT})
\t\t(property "Footprint" "Pkg:SOIC-8" (at -2.794 0 0) {FONT})
\t\t(property "Datasheet" "http://par" (at 0 0 0) {FONT})
\t\t(symbol "PAR_0_1" (rectangle (start -5 5) (end 5 -5)))
\t\t(symbol "PAR_1_1" (pin input line (at -7.62 0 0) (length 2.54) (name "A" {FONT}) (number "1" {FONT})))
\t)
\t(symbol "KID"
\t\t(extends "PAR")
\t\t(property "Reference" "U" (at 0 5 0) {FONT})
\t\t(property "Value" "KID" (at 0 -5 0) {FONT})
\t\t(property "Footprint" "" (at 0 0 0) {FONT})
\t\t(property "Datasheet" "http://kid" (at 1 1 0) {FONT})
\t\t(property "Extra" "x" (at 2 2 0) {FONT})
\t)
\t(symbol "GRANDKID"
\t\t(extends "KID")
\t\t(property "Value" "GRANDKID" (at 0 -5 0) {FONT})
\t)
)
'''


@pytest.fixture
def lib(tmp_path):
    (tmp_path / "T.kicad_sym").write_text(LIB)
    return Lib(tmp_path)


def test_empty_mandatory_keeps_parent_field_and_child_fields_added(lib):
    e = lib.embedded("T:KID")
    assert '(symbol "T:KID"' in e and "extends" not in e
    assert '"Footprint" "Pkg:SOIC-8" (at -2.794 0 0)' in e  # empty child Footprint -> parent's whole field
    assert '"Datasheet" "http://kid" (at 1 1 0)' in e and "http://par" not in e
    assert '"Value" "KID"' in e and '"Extra" "x"' in e
    assert '(symbol "KID_0_1"' in e and '(symbol "KID_1_1"' in e and "PAR_" not in e


def test_recursive_extends(lib):
    e = lib.embedded("T:GRANDKID")
    assert '(symbol "T:GRANDKID"' in e and '"Extra" "x"' in e and '"Value" "GRANDKID"' in e
    assert '(symbol "GRANDKID_1_1"' in e and "KID_1_1" not in e.replace("GRANDKID_1_1", "")


STOCK = Path(os.environ.get("OHMAI_KICAD_SYMBOLS",
                            Path.home() / ".cache/ohmai/kicad-libs/kicad-symbols-9.0.9.1"))


@pytest.mark.skipif(not shutil.which("kicad-cli") or not (STOCK / "Sensor_Temperature.kicad_sym").is_file(),
                    reason="needs kicad-cli and stock KiCad 9 symbols")
@pytest.mark.parametrize("lib_id", ["Sensor_Temperature:LM75B", "Transistor_BJT:BCV49"])
def test_kicad_erc_no_lib_symbol_mismatch(tmp_path, lib_id):
    name = lib_id.split(":")[1]
    lib = Lib(STOCK)
    emb = lib.embedded(lib_id)
    nick = lib_id.split(":")[0]
    (tmp_path / "sym-lib-table").write_text(
        f'(sym_lib_table (version 7)\n  (lib (name "{nick}")(type "KiCad")'
        f'(uri "{STOCK}/{nick}.kicad_sym")(options "")(descr ""))\n)\n')
    (tmp_path / "t.kicad_pro").write_text("{}\n")  # project context so the sym-lib-table is used
    sch = tmp_path / "t.kicad_sch"
    sch.write_text(f'''(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0")
\t(uuid "11111111-1111-1111-1111-111111111111")
\t(paper "A4")
\t(lib_symbols
{emb}
\t)
\t(symbol (lib_id "{lib_id}") (at 100 100 0) (unit 1) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp yes)
\t\t(uuid "22222222-2222-2222-2222-222222222222")
\t\t(property "Reference" "U7" (at 100 90 0) {FONT})
\t\t(property "Value" "{name}" (at 100 110 0) {FONT})
\t\t(instances (project "t" (path "/11111111-1111-1111-1111-111111111111" (reference "U7") (unit 1))))
\t)
\t(sheet_instances (path "/" (page "1")))
)
''')
    rpt = tmp_path / "r.rpt"
    r = subprocess.run(["kicad-cli", "sch", "erc", str(sch), "-o", str(rpt)], capture_output=True, text=True,
                       cwd=tmp_path)
    assert "Failed to load" not in r.stdout + r.stderr and rpt.is_file(), r.stdout + r.stderr
    text = rpt.read_text()
    assert "lib_symbol_issues" not in text or "not found" not in text, text  # library really resolved
    assert "lib_symbol_mismatch" not in text
