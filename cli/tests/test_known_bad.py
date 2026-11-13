# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Known-bad suite: each fixture's planted mistake is caught by its check, and its clean twin passes."""

from __future__ import annotations

import pytest
import yaml

from ohmai import kicad, knownbad, resources
from ohmai.checks import HELP, REGISTRY
from ohmai.netlist import load

CASES = knownbad.load_cases()


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_planted_bug_is_caught(case, tmp_path, monkeypatch):
    monkeypatch.setenv("OHMAI_CACHE", str(tmp_path / "cache"))
    miss = knownbad.missing_needs(case.spec.get("needs") or [])
    if miss:
        pytest.skip("needs " + ", ".join(miss))
    o = knownbad.run_case(case, tmp_path)
    assert not o.error, o.error
    assert o.caught, o.problems


def test_every_check_has_a_planted_bug():
    covered = {c.check.split(":")[0] if c.check.startswith(("review:", "docs:")) else c.check for c in CASES}
    names = set(REGISTRY) | {k for k in HELP if k == "review"} | {"docs"}
    missing = sorted(n for n in names if n not in covered and n not in knownbad.NOT_COVERED)
    assert not missing, f"checks without a known-bad case (add one or a reason in NOT_COVERED): {missing}"


def test_cases_are_well_formed():
    for c in CASES:
        assert c.spec.get("check") and c.spec.get("bug") and c.spec.get("detail"), c.name
        assert c.spec.get("mutate") or c.spec.get("bad_opts"), f"{c.name}: no planted mistake"
        exp = c.spec.get("expect") or {}
        if exp.get("bad", "FAIL") != "FAIL":
            assert c.spec.get("why_bad_warn"), f"{c.name}: a non-FAIL expectation needs a reason"
        if exp.get("clean", "PASS") != "PASS":
            assert c.spec.get("why_clean_warn"), f"{c.name}: a non-PASS clean twin needs a reason"


def test_broken_fixture_is_an_error_not_a_pass(tmp_path):
    case = knownbad.Case("broken", CASES[0].dir, {**CASES[0].spec, "mutate": [
        {"file": "board.net.yaml", "replace": "text that is not there", "with": "x"}]})
    o = knownbad.run_case(case, tmp_path)
    assert o.error and not o.caught


@pytest.mark.skipif(not kicad.kicad_cli() or knownbad.missing_needs(["kicad-libs"]), reason="needs kicad-cli + libs")
def test_bases_match_a_fresh_golden_build(tmp_path):
    """_base/ldo-led/board.net.yaml and _base/ldo-led-sheets are the golden board as built today."""
    case = knownbad.Case("probe", tmp_path, {"check": "netlist", "base": ["golden:ldo-led"], "name": "ldo-led",
                                             "build": True})
    p, _ = knownbad.build_twin(case, tmp_path / "p", bad=False)
    from ohmai.checks.schematic import export_netlist

    nl = load(export_netlist(p, {}))
    spec = yaml.safe_load((knownbad.fixtures_dir() / "_base/ldo-led/files/board.net.yaml").read_text())
    assert {r: (c["value"], c["part"], c["fields"].get("MPN"), c["footprint"]) for r, c in nl.comps.items()} == {
        r: (str(c["value"]), c["part"], c.get("mpn"), c.get("footprint")) for r, c in spec["parts"].items()}
    assert {n: sorted(v) for n, v in nl.nets.items()} == {n: sorted(v) for n, v in spec["nets"].items()}
    for f in ("ldo-led", "power", "led"):
        want = (knownbad.fixtures_dir() / f"_base/ldo-led-sheets/files/hardware/{f}.kicad_sch").read_text()
        assert (p.root / "hardware" / f"{f}.kicad_sch").read_text() == want, f


def test_fixtures_live_in_tests_dir():
    assert (resources.tests_dir() / "fixtures" / "known-bad").is_dir()
