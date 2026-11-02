"""Dogfooding fixes: decision-log sync, unbound connectors/ICs, passive-mpn, status --set-phase/--gate."""

from types import SimpleNamespace

from ohmai.checks import FAIL, PASS, SKIP
from ohmai.checks.decisions import decisions
from ohmai.checks.pinmap import unbound
from ohmai.netlist import Netlist


def _proj(tmp_path, status, log=None, res=None):
    (tmp_path / "STATUS.md").write_text(status)
    if log is not None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "decisions.md").write_text(log)
    if res is not None:
        (tmp_path / "reviews" / "schematic").mkdir(parents=True)
        (tmp_path / "reviews" / "schematic" / "resolutions.md").write_text(res)
    return SimpleNamespace(root=tmp_path)


LOG = "| ID | Date |\n|---|---|\n| D-001 | x | see also D-002 |\n## D-003: heading entry\n"


def test_decisions_pass(tmp_path):
    r = decisions(_proj(tmp_path, "| D-001 | locked |\n| D-003 |", LOG), {})
    assert r.status == PASS, r.details


def test_decisions_status_only_fails(tmp_path):
    # a waiver in STATUS, mentioned in another row, never logged itself
    r = decisions(_proj(tmp_path, "| D-001 |\n| D-002 | waiver |", LOG), {})
    assert r.status == FAIL and "D-002" in r.details[0]


def test_decisions_resolutions_ref_fails(tmp_path):
    r = decisions(_proj(tmp_path, "| D-001 |", LOG, "accepted_independence: [{ref: D-007}]"), {})
    assert r.status == FAIL and "reviews/schematic/resolutions.md" in r.details[0]


def test_decisions_missing_log_and_skip(tmp_path):
    assert decisions(_proj(tmp_path, "| D-001 |"), {}).status == FAIL
    assert decisions(_proj(tmp_path / ".." / tmp_path.name, "no ids"), {}).status in (SKIP, FAIL)


def test_unbound_connector_warns():
    nl = Netlist()
    nl.libpins = {"C:J8": {str(i): "" for i in range(1, 9)}, "C:J2": {"1": "", "2": ""}, "C:U": {str(i): "" for i in range(5)}}
    nl.comps = {"J4": {"part": "C:J8"}, "J9": {"part": "C:J2"}, "U1": {"part": "C:U"}, "R1": {"part": "C:J8"},
                "#PWR1": {"part": "C:J8"}}
    out = unbound(nl, {"U1"})
    assert len(out) == 1 and out[0].startswith("unbound: J4")
