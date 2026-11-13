# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Golden projects: snapshot compare/accept logic (no KiCad) and the real rebuild (KiCad + libs)."""

import shutil

import pytest
import yaml

from ohmai import golden as gd
from ohmai import resources
from ohmai.knownbad import missing_needs


def _fake(tmp_path):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "golden.yaml").write_text("name: demo\nartefacts: [netlist]\n")
    return gd.load_goldens(root=tmp_path)[0]


def test_compare_and_accept_roundtrip(tmp_path):
    g = _fake(tmp_path)
    out = {"netlist": "GND: C1.2 U1.2(GND)\n"}
    assert set(gd.compare(g, out)) == {"netlist"}, "no snapshot yet must count as a difference"
    run = gd.GoldenRun(g, outputs=out, diffs=gd.compare(g, out), toolchain={"kicad-cli": "9.0.2"})
    with pytest.raises(SystemExit):
        gd.accept(run, "  ")
    assert gd.accept(run, "first snapshot", by="test") == ["netlist"]
    assert gd.compare(g, out) == {}
    d = gd.compare(g, {"netlist": "GND: C1.2\n"})
    assert "-GND: C1.2 U1.2(GND)" in d["netlist"] and "+GND: C1.2" in d["netlist"]
    run2 = gd.GoldenRun(g, outputs={"netlist": "GND: C1.2\n"}, diffs=d, toolchain={"kicad-cli": "9.0.3"})
    gd.accept(run2, "U1 removed on purpose")
    m = yaml.safe_load((g.snapshots / "MANIFEST.yaml").read_text())
    assert [a["reason"] for a in m["accepted"]] == ["first snapshot", "U1 removed on purpose"]
    assert m["toolchain"] == {"kicad-cli": "9.0.3"} and m["accepted"][1]["changed"] == ["netlist"]
    assert "&id" not in (g.snapshots / "MANIFEST.yaml").read_text()


def test_unknown_golden_is_an_error(tmp_path):
    _fake(tmp_path)
    with pytest.raises(SystemExit):
        gd.load_goldens(["nope"], root=tmp_path)


def test_lib_symbol_blocks():
    text = '(kicad_sch (lib_symbols (symbol "Device:R" (pin "a\\")")) (symbol "power:GND" (x 1))) (symbol (lib_id "x")))'
    names = [n for n, _ in gd._blocks(text, text.index("(lib_symbols"))]
    assert names == ["Device:R", "power:GND"]


def test_shipped_goldens_are_complete():
    goldens = gd.load_goldens(root=resources.tests_dir() / "golden")
    assert "ldo-led" in [g.name for g in goldens]
    for g in goldens:
        arts = g.spec.get("artefacts") or []
        assert set(arts) <= set(gd.ARTEFACTS), g.name
        for a in arts:
            assert (g.snapshots / f"{a}.txt").is_file(), f"{g.name}: no approved snapshot for {a}"
        m = gd.manifest(g)
        assert m.get("accepted") and all(e.get("reason") for e in m["accepted"]), g.name


LDO = gd.load_goldens(["ldo-led"], root=resources.tests_dir() / "golden")[0]
needs = pytest.mark.skipif(bool(missing_needs(LDO.spec.get("needs") or [])),
                           reason="needs " + ", ".join(missing_needs(LDO.spec.get("needs") or [])))


@needs
def test_ldo_led_matches_snapshots():
    r = gd.run_golden(LDO)
    assert r.status == "PASS", (r.error, r.diffs)


@needs
def test_design_change_is_caught(tmp_path):
    root = tmp_path / "golden"
    shutil.copytree(LDO.dir, root / "ldo-led")
    led = root / "ldo-led/src/hardware/gen/layout/led.yaml"
    led.write_text(led.read_text().replace("value: 1k", "value: 470"))
    r = gd.run_golden(gd.load_goldens(root=root)[0])
    assert r.status == "DIFF" and {"netlist", "bom"} <= set(r.diffs)
    assert "+R1 | 470 |" in r.diffs["netlist"]
