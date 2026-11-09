# SPDX-License-Identifier: Apache-2.0
"""mcu-pin-budget: pin shortfall fails at Phase 2/4, not at the Phase 7 MCU sheet."""

from __future__ import annotations

import yaml

from ohmai.checks import FAIL, PASS, SKIP, WARN, run
from ohmai.checks.mcubudget import evaluate
from ohmai.cli import main
from ohmai.phases import BY_ID
from ohmai.project import find_project

SMALL_MCU = {  # MCU-TSSOP20 TSSOP-20: ~11 free GPIO vs ~16 nets (a dogfooding lesson)
    "mcu": "MCU-TSSOP20", "package": "TSSOP-20", "total_pins": 20,
    "reserved": {"power": ["VDD", "VDDA"], "ground": ["VSS"], "reset": ["NRST"], "boot": ["BOOT0"],
                 "swd": ["PA13", "PA14"], "fixed_serial": ["PA9", "PA10"]},
    "required_nets": [{"net": f"N{i}", "kind": "control"} for i in range(16)],
}


def _proj(tmp_path, monkeypatch):
    d = tmp_path / "p"
    assert main(["new", str(d), "--idea", "x", "--no-git"]) == 0
    monkeypatch.chdir(d)
    return find_project(d)


def test_shortfall_fails():
    r = evaluate(SMALL_MCU)
    assert r.status == FAIL and "SHORTFALL" in r.summary and "11" in r.summary


def test_fits_passes_and_min_spare():
    ok = {**SMALL_MCU, "required_nets": [f"N{i}" for i in range(10)]}
    assert evaluate(ok).status == PASS
    assert evaluate({**ok, "min_spare": 2}).status == FAIL
    assert evaluate({**ok, "free_gpio": 9}).status == FAIL


def test_incomplete_budget_fails():
    assert evaluate({"total_pins": 20}).status == FAIL
    assert evaluate({**SMALL_MCU, "reserved": {"power": 2}}).status == FAIL  # no ground/swd
    assert evaluate({**SMALL_MCU, "required_nets": ["A", "A"]}).status == FAIL
    assert evaluate({"mcu": "none"}).status == PASS


def test_missing_file_skip_or_warn(tmp_path, monkeypatch):
    p = _proj(tmp_path, monkeypatch)
    arch = p.root / "docs" / "architecture.md"
    arch.write_text("# Arch\nbuck only\n", encoding="utf-8")
    sel = p.root / "parts" / "selection.md"
    if sel.exists():
        sel.write_text("# Parts\n", encoding="utf-8")
    (p.root / "bom" / "bom.csv").write_text("ref,mpn\n", encoding="utf-8")
    assert run(p, "mcu-pin-budget", {}).status == SKIP
    arch.write_text("# Arch\nMCU: MCU-TSSOP20\n", encoding="utf-8")
    assert run(p, "mcu-pin-budget", {}).status == WARN
    (p.root / "parts" / "mcu_pin_budget.yaml").write_text(yaml.safe_dump(SMALL_MCU), encoding="utf-8")
    assert run(p, "mcu-pin-budget", {}).status == FAIL


def test_in_phases():
    for ph in ("architecture", "parts", "constraints"):
        assert "mcu-pin-budget" in BY_ID[ph].checks
