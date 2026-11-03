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


# --- passive-mpn -------------------------------------------------------------------------------------
from ohmai.checks.passivempn import compare, decode, summarize  # noqa: E402


def test_decode_vendor_codes():
    assert decode("0402WGF1000TCE")[2] == 100.0
    assert decode("0603WAF1002T5E")[2] == 10000.0
    assert decode("RC0402FR-074K7L")[2] == 4700.0
    assert abs(decode("CL10A105KB8NNNC")[2] - 1e-6) < 1e-12
    assert abs(decode("CL31B106KBHNNNE")[2] - 10e-6) < 1e-12
    assert abs(decode("GRM155R71C104KA88D")[2] - 100e-9) < 1e-15
    assert decode("FICT-R-1K") is None


def test_compare_flags_value_mismatch():
    comps = {"R7": {"value": "330R", "fields": {"MPN": "0402WGF1000TCE"}},
             "R4": {"value": "10k", "fields": {"MPN": "0402WGF1002TCE"}},
             "C1": {"value": "100n", "fields": {"MPN": "GRM155R71C104KA88D"}},
             "C2": {"value": "1u", "fields": {"MPN": "0402WGF1002TCE"}},
             "R9": {"value": "1k", "fields": {"MPN": "XYZ"}}}
    errs, unknown, n = compare(comps)
    assert any(e.startswith("R7:") for e in errs) and any(e.startswith("C2:") for e in errs)
    assert not any(e.startswith(("R4:", "C1:")) for e in errs)
    assert unknown and unknown[0].startswith("R9:")


# --- ohmai status --set-phase/--gate ------------------------------------------------------------------
import pytest  # noqa: E402

from ohmai.status import load as load_status  # noqa: E402
from ohmai.status import set_front  # noqa: E402

FM = "---\nproject: x\nphase: 1            # 1-12\ngate: open          # open | in_review | approved\nupdated: 2026-01-01\n---\n# body\n"


def test_set_front_phase_gate(tmp_path):
    st = tmp_path / "STATUS.md"
    st.write_text(FM)
    ch = set_front(st, "schematic", "in_review", today="2026-11-03")
    t = st.read_text()
    assert "phase: 7            # 1-12" in t and "gate: in_review          # open" in t and "updated: 2026-11-03" in t
    assert t.endswith("# body\n") and ch[0] == "phase: 1 -> 7"
    assert load_status(st).meta["phase"] == 7


def test_set_front_approved_needs_logged_decision(tmp_path):
    st = tmp_path / "STATUS.md"
    st.write_text(FM)
    with pytest.raises(ValueError):
        set_front(st, gate="approved")
    with pytest.raises(ValueError):
        set_front(st, gate="approved", decision="D-004")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "decisions.md").write_text("| D-004 | x |\n")
    set_front(st, gate="approved", decision="D-004")
    assert "gate: approved" in st.read_text()
    with pytest.raises(ValueError):
        set_front(st, phase="99")


def test_passive_mpn_reports_skipped_count():
    comps = {"R1": {"value": "1k", "fields": {"MPN": "0603WAF1001T5E"}},
             "R2": {"value": "1k", "fields": {"MPN": "ERJ-3EKF1001V"}},
             "C1": {"value": "1u", "fields": {"MPN": "XYZ-1U"}}}
    errs, unknown, n = compare(comps)
    assert (n, errs, len(unknown)) == (1, [], 2)
    assert "skipped 2 MPN(s) not decodable" in summarize(n, errs, unknown)
