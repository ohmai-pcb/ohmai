"""Feedback 2026-11 checks: generator intent vs KiCad, wire ends, unlabeled-net coverage, label policy, sheets."""

from pathlib import Path

import pytest
import yaml

from helpers import good_tps63001, netlist

from ohmai import sheets
from ohmai.checks.gencheck import compare_intent, wire_problems
from ohmai.checks.netdiff import coverage, load_expected
from ohmai.checks.readability import analyze
from ohmai.netlist import load


def _nl(tmp_path, comps, nets, lib):
    p = tmp_path / "t.net"
    p.write_text(netlist(comps, nets, lib))
    return load(p)


def _intent():
    return {"U1.10": "VCC", "C1.1": "VCC", "U1.4": "FB", "R2.2": "FB", "R3.1": "FB", "U1.1": "NC"}


def test_gen_netlist_matches_good(tmp_path):
    errs, _ = compare_intent(_nl(tmp_path, *good_tps63001()), _intent())
    assert errs == [], errs


def test_gen_netlist_split_merged_missing_nc(tmp_path):
    comps, nets, lib = good_tps63001()
    nets["/FB"] = ["U1.4", "R2.2"]; nets["/FB2"] = ["R3.1"]                  # wire stopped short: split
    errs, _ = compare_intent(_nl(tmp_path, comps, nets, lib), _intent())
    assert any(e.startswith("split: generator net FB") for e in errs), errs
    comps, nets, lib = good_tps63001()
    nets["/VCC"] += nets.pop("/FB")                                         # VCC shorted to FB
    errs, _ = compare_intent(_nl(tmp_path, comps, nets, lib), _intent())
    assert any(e.startswith("merged:") for e in errs), errs
    comps, nets, lib = good_tps63001()
    nets["/N1"] = ["U1.1", "C1.1"]; nets["/VCC"] = ["U1.10"]                # NC pin wired + C1.1 moved
    errs, _ = compare_intent(_nl(tmp_path, comps, nets, lib), {**_intent(), "C9.1": "VCC"})
    assert any(e.startswith("nc: generator says U1.1") for e in errs), errs
    assert any(e.startswith("missing: VCC: C9.1") for e in errs), errs


def test_gen_netlist_empty_netlist_fails(tmp_path):
    errs, _ = compare_intent(_nl(tmp_path, {}, {}, {}), _intent())
    assert errs and errs[0].startswith("empty:")


def _sch(body):
    return ('(kicad_sch (version 20250114) (generator "t") (uuid "u") (paper "A4")\n'
            '(lib_symbols (symbol "Device:R" (symbol "R_1_1" '
            '(pin passive line (at 0 3.81 270) (length 1.27) (name "~") (number "1"))'
            '(pin passive line (at 0 -3.81 90) (length 1.27) (name "~") (number "2")))))\n'
            '(symbol (lib_id "Device:R") (at 50.8 50.8 0) (property "Reference" "R1" (at 0 0 0)))\n' + body + ")")


def test_wire_ends_clean_truncated_and_mid_wire():
    # R1 pins at (50.8, 46.99) and (50.8, 54.61) (KiCad y down: pin 1 at y-3.81)
    ok = '(wire (pts (xy 50.8 46.99) (xy 50.8 40.64)))(label "A" (at 50.8 40.64 0))'
    assert wire_problems(_sch(ok)) == []
    trunc = '(wire (pts (xy 50.8 46.99) (xy 50.8 40.64)))'
    assert any("truncated wire" in p for p in wire_problems(_sch(trunc))), wire_problems(_sch(trunc))
    mid = '(wire (pts (xy 50.8 40.64) (xy 50.8 60.96)))(label "A" (at 50.8 40.64 0))(label "B" (at 50.8 60.96 0))'
    probs = wire_problems(_sch(mid))
    assert any("R1.1" in p and "mid-wire" in p for p in probs), probs


def test_unlabeled_nets_must_be_listed(tmp_path):
    comps, nets, lib = good_tps63001()
    nets["Net-(U1-FB)"] = nets.pop("/FB")
    nl = _nl(tmp_path, comps, nets, lib)
    csv = tmp_path / "e.csv"
    csv.write_text("net,nodes\nVCC,U1.10,C1.1\n")
    cov = coverage(nl, load_expected(csv)[0])
    assert cov["auto"] == 1 and cov["auto_missing"] == 1 and "Net-(U1-FB)" in cov["errors"][0]
    csv.write_text("net,nodes\nVCC,U1.10,C1.1\nFB,U1.4,R2.2,R3.1\n")
    cov = coverage(nl, load_expected(csv)[0])
    assert cov["auto_missing"] == 0 and cov["errors"] == []


def test_label_policy_rail_and_label():
    body = ('(wire (pts (xy 25.4 25.4) (xy 50.8 25.4)) (uuid "w1"))\n'
            '(label "X" (at 25.4 25.4 0) (effects (font (size 1.27 1.27))) (uuid "l1"))')
    sch = '(kicad_sch (version 20250114) (generator "t") (uuid "u") (paper "A4") (lib_symbols)\n' + body + "\n)"
    fails, _ = analyze(sch)
    assert any("label" in f.lower() for f in fails)
    fails, _ = analyze(sch, label_policy="rail-and-label")
    assert not any("local label" in f.lower() for f in fails), fails


def test_sheet_instances_and_resolve(tmp_path):
    root = tmp_path / "b.kicad_sch"
    root.write_text('(kicad_sch (version 20250114) (lib_symbols)'
                    '(sheet (at 0 0) (property "Sheetname" "1-power") (property "Sheetfile" "power.kicad_sch")))')
    (tmp_path / "power.kicad_sch").write_text("(kicad_sch (version 20250114) (lib_symbols))")
    names = [s.name for s in sheets.instances(root)]
    assert names == ["root", "1-power"]
    assert sheets.resolve(root, "power").path == "/1-power/"
    assert sheets.symbol_count(root.read_text()) == 1           # root with only a sheet symbol is not empty
    assert sheets.symbol_count((tmp_path / "power.kicad_sch").read_text()) == 0


# ---------- adopt mode, N/A, embedded symbols, --sheet ----------

@pytest.fixture
def proj(tmp_path, monkeypatch):
    from ohmai.cli import main
    from ohmai.project import find_project

    d = tmp_path / "demo"
    assert main(["new", str(d), "--idea", "test board", "--no-git"]) == 0
    monkeypatch.chdir(d)
    return find_project(d)


def test_na_needs_a_reason(proj):
    from ohmai.checks import run
    from ohmai.project import find_project

    cfg = proj.root / "ohmai.yaml"
    cfg.write_text(cfg.read_text().replace("checks:\n", "checks:\n  na: {erc: 'adopted: legacy sheet', passives: ''}\n", 1))
    p = find_project(proj.root)
    r = run(p, "erc", {})
    assert r.status == "SKIP" and r.summary == "N/A: adopted: legacy sheet"
    assert run(p, "passives", {}).status == "FAIL"


def test_adopt_writes_baseline_and_never_overwrites(tmp_path):
    from ohmai.adopt import adopt

    root = tmp_path / "foreign"
    (root / "hw").mkdir(parents=True)
    (root / "hw" / "b.kicad_pro").write_text("{}")
    (root / "hw" / "b.kicad_sch").write_text("(kicad_sch (version 20250114))")
    comps, nets, lib = good_tps63001()
    nets["Net-(U1-FB)"] = nets.pop("/FB")
    nets["unconnected-(U1-EN-Pad1)"] = nets.pop("/N1")
    nl = tmp_path / "x.net"
    nl.write_text(netlist(comps, nets, lib))
    (root / "expected_nets.csv").write_text("net,nodes\nKEEP,U1.10,C1.1\n")
    log = adopt(root, netlist=nl)
    cfg = yaml.safe_load((root / "ohmai.yaml").read_text())
    assert cfg["hardware"]["schematic"] == "hw/b.kicad_sch"
    assert all(v for v in cfg["checks"]["na"].values()) and "gen-netlist" in cfg["checks"]["na"]
    assert (root / "expected_nets.csv").read_text() == "net,nodes\nKEEP,U1.10,C1.1\n"      # untouched
    new = (root / "expected_nets.adopted.csv").read_text()
    assert "Net-(U1-FB),R2.2,R3.1,U1.4" in new and "\nNC,U1.1" in new, new
    assert any("left untouched" in line for line in log)
    with pytest.raises(SystemExit):
        adopt(root, netlist=nl)                    # both files exist: refuse rather than overwrite


def test_lib_refs_embedded_only_with_pin_map(proj, monkeypatch):
    from ohmai.checks import librefs
    from ohmai.project import find_project

    cfg = proj.root / "ohmai.yaml"
    assert librefs.embedded_policy(find_project(proj.root)) is None          # default: vetted libraries only
    cfg.write_text(cfg.read_text().replace("checks:\n", "checks:\n  lib_refs: {allow_embedded: true}\n", 1))
    c = proj.root / "parts" / "constraints"
    c.mkdir(parents=True, exist_ok=True)
    (c / "W5500.yaml").write_text("part: W5500\nmatch: {mpn: W5500, lib_symbol: W5500}\n")
    pol = librefs.embedded_policy(find_project(proj.root))
    assert pol == {"W5500": "W5500.yaml"}


def test_cli_sheet_unknown_name_exits(proj, monkeypatch):
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "ohmai", "check", "schematic", "--sheet", "nope"], cwd=proj.root,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "nope" in (r.stderr + r.stdout)
    r = subprocess.run([sys.executable, "-m", "ohmai", "check", "layout", "--sheet", "root"], cwd=proj.root,
                       capture_output=True, text=True)
    assert r.returncode != 0
