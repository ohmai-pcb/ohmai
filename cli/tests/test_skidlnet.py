# SPDX-License-Identifier: Apache-2.0
"""skidl-netlist: partition compare (no SKiDL needed), WARN without SKiDL, SKiDL stays out of process."""

import sys

from ohmai.checks import run
from ohmai.checks.skidlnet import compare
from ohmai.cli import main
from ohmai.netlist import Netlist
from ohmai.project import find_project


def nl(nets):
    n = Netlist(nets={k: set(v) for k, v in nets.items()})
    n.where = {x: k for k, v in nets.items() for x in v}
    return n


def row(name, nodes, open_=False, nc=False):
    return {"name": name, "nodes": nodes, "refs": [], "open": open_, "nc": nc}


SK = nl({"VIN": ["J1.1", "U1.1"], "3V3": ["U1.5", "R1.1"], "NC:U1.4": ["U1.4"]})
ROWS = [row("VIN", ["J1.1", "U1.1"]), row("3V3", ["U1.5", "R1.1"]), row("NC:U1.4", ["U1.4"], nc=True)]


def test_match():
    ki = nl({"Net-(J1-Pin_1)": ["J1.1", "U1.1"], "/3V3": ["U1.5", "R1.1"], "unconnected-(U1-NC-Pad4)": ["U1.4"]})
    assert compare(SK, ki, ROWS) == []


def test_short_split_extra_nc():
    ki = nl({"/VIN": ["J1.1", "U1.1", "U1.5", "R1.1"], "x": ["U1.4"]})
    assert any(e.startswith("short: expected nets VIN and 3V3") for e in compare(SK, ki, ROWS))
    ki = nl({"a": ["J1.1"], "b": ["U1.1"], "/3V3": ["U1.5", "R1.1"], "x": ["U1.4"]})
    assert any(e.startswith("split: expected net VIN") for e in compare(SK, ki, ROWS))
    ki = nl({"/VIN": ["J1.1", "U1.1", "C1.1"], "/3V3": ["U1.5", "R1.1"], "x": ["U1.4"]})
    assert any(e.startswith("extra: KiCad net VIN") and "C1.1" in e for e in compare(SK, ki, ROWS))
    open_rows = [row("VIN", ["J1.1", "U1.1"], open_=True), *ROWS[1:]]
    assert compare(SK, ki, open_rows) == [], "an open row (...) allows extra pins"
    ki = nl({"/VIN": ["J1.1", "U1.1"], "/3V3": ["U1.5", "R1.1", "U1.4"]})
    errs = compare(SK, ki, ROWS)
    assert any(e.startswith("nc: U1.4") or e.startswith("short") for e in errs), errs


def test_warn_without_skidl(tmp_path, monkeypatch):
    d = tmp_path / "p"
    assert main(["new", str(d), "--idea", "x", "--no-git"]) == 0
    monkeypatch.chdir(d)
    r = run(find_project(d), "skidl-netlist", {"skidl_python": str(tmp_path / "no-python")})
    assert r.status == "WARN" and "SKiDL not installed" in r.summary


def test_skidl_never_imported_in_process():
    import ohmai.checks.skidlnet  # noqa: F401

    assert "skidl" not in sys.modules and "InSpice" not in sys.modules
