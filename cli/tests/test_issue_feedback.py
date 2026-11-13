# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Regression evidence for issue #1: malformed BOMs and bounded check loops."""
import csv
import json

import pytest

from ohmai.attempts import normalize, record, signature
from ohmai.checks import FAIL, PASS, Result, run
from ohmai.checks.bom import bom_schema, read_rows
from ohmai.cli import main
from ohmai.project import find_project


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "board"
    assert main(["new", str(root), "--idea", "fictional fixture", "--no-git"]) == 0
    monkeypatch.chdir(root)
    return find_project(root)


@pytest.mark.parametrize("a,b", [
    ("power: pin U1.7 at (10.16, -4) sits mid-wire", "power: pin U1.7 at (11.43, 99) sits mid-wire"),
    ("net SDA blocked cell [10, 20]", "net SDA blocked cell [11, 21]"),
    ("net SDA blocked x=10 y=-20.2", "net SDA blocked x=11 y=22"),
    ("power: wires cross at (10,20)-(30,40)", "power: wires cross at (11,21)-(31,41)"),
])
def test_normalize_coordinates(a, b):
    assert normalize(a) == normalize(b)


def test_signature_retains_identity():
    def fail(detail, name="wire-ends"):
        return Result(name, FAIL, "varying count ignored", [detail])
    a = fail("power: pin U1.7 at (1,2) blocked net SDA")
    b = fail("power: pin U1.8 at (1,2) blocked net SDA")
    c = fail("power: pin U1.7 at (1,2) blocked net SCL")
    assert signature([a], "power") != signature([b], "power")
    assert signature([a], "power") != signature([c], "power")
    assert signature([a], "power") != signature([a], "led")
    assert signature([a], "power") != signature([fail(a.details[0], "readability")], "power")
    assert signature([a, b]) == signature([b, a])


def test_signature_canonicalizes_normalized_detail_set(tmp_path):
    a = Result("wire-ends", FAIL, "2 problems", [
        "pin R1.1 at (1,2) sits mid-wire", "pin R1.2 at (3,4) sits mid-wire",
    ])
    b = Result("wire-ends", FAIL, "3 diagnostics", [
        "pin R1.2 at (9,8) sits mid-wire", "pin R1.1 at (7,6) sits mid-wire",
        "pin R1.1 at (5,4) sits mid-wire",
    ])
    assert signature([a], "power") == signature([b], "power")
    for count, result in enumerate((a, b, a), 1):
        assert len(record(tmp_path, [result], "power", "check")["attempts"]) == count
    # All normalized identities matter, not just the stable first representative.
    b.details[0] = "pin R1.3 at (9,8) sits mid-wire"
    assert signature([a], "power") != signature([b], "power")


def test_count_reset_and_saturation(tmp_path):
    failed = [Result("wire-ends", FAIL, "bad", ["pin U1.1 at (1,2) blocked"])]
    for count in (1, 2, 3, 3):
        state = record(tmp_path, failed, "power", "ohmai check wire-ends")
        assert len(state["attempts"]) == count
    assert len(record(tmp_path, failed, "led", "check")["attempts"]) == 1
    assert record(tmp_path, [], "led", "check")["attempts"] == []
    assert len(record(tmp_path, failed, "led", "check")["attempts"]) == 1
    failed[0].details = ["pin U1.2 blocked"]
    assert len(record(tmp_path, failed, "led", "check")["attempts"]) == 1


def test_cli_third_failure_and_json(project, monkeypatch, capsys):
    import ohmai.checks
    from ohmai.phases import SHEET_CHECKS
    from ohmai.sheets import resolve

    sheet = resolve(project.schematic, "root")
    index = 0

    def fake_run(p, name, opts):
        nonlocal index
        if name == "wire-ends":
            index += 1
            return Result(name, FAIL, "1 problem", [f"pin U1.7 at ({index},2) sits mid-wire"])
        return Result(name, PASS, "fixture")

    monkeypatch.setattr(ohmai.checks, "run", fake_run)
    args = ["check", "schematic", "--sheet", "root", "--json"]
    for code in (1, 1, 3, 3):
        assert main(args) == code
        output = capsys.readouterr()
        assert len(json.loads(output.out)) == len(SHEET_CHECKS)
        if code == 3:
            assert "Attempt 3:" in output.err
            assert "Hypothesis: ____" in output.err and "rail-and-label" in output.err
    history = json.loads((project.out / "check-attempts.json").read_text())
    assert sheet.file.name in history["signature"]
    assert len(history["attempts"]) == 3
    monkeypatch.setattr(ohmai.checks, "run", lambda p, name, opts: Result(name, PASS, "fixture"))
    assert main(args) == 0
    assert json.loads((project.out / "check-attempts.json").read_text())["attempts"] == []


@pytest.mark.parametrize("row,actual", [("U1,1,extra", 3), ("U1", 1)])
def test_ragged_csv(row, actual):
    with pytest.raises(csv.Error, match=rf"row 2 \(U1\): field-count mismatch: expected 2, got {actual}"):
        read_rows("refs,qty\n" + row)


def test_csv_quoted_commas_and_blank_fields():
    assert read_rows('refs,notes,qty\nU1,"a,b",\n')[1] == [{"refs": "U1", "notes": "a,b", "qty": ""}]


def test_ragged_bom_fails_not_crashes(project):
    project.bom.write_text("refs,qty\nU1,1,unquoted comma\n")
    result = bom_schema(project, {})
    assert result.status == FAIL and "row 2 (U1)" in result.details[0]
    # Other BOM readers also fail closed through the registry, not an exception.
    assert run(project, "bom-diff", {}).status == FAIL


@pytest.mark.parametrize("corruption", [
    "not-json", "not-object", "missing-signature", "bad-signature", "missing-attempts",
    "bad-attempts", "empty-attempts", "empty-records", "integer-records", "too-many",
    "missing-command", "bad-command", "missing-changed", "bad-changed",
    "missing-failures", "bad-failures", "empty-failures", "integer-failure",
    "missing-name", "bad-name", "missing-status", "bad-status", "unknown-status",
    "missing-summary", "bad-summary", "missing-details", "bad-details", "integer-detail",
    "cleared-with-attempts",
])
@pytest.mark.parametrize("outcome", ["matching", "changed", "pass"])
def test_corrupt_attempt_history_stops(project, monkeypatch, capsys, corruption, outcome):
    import ohmai.checks

    failure = Result("wire-ends", FAIL, "blocked")
    state = {"signature": signature([failure]), "attempts": [
        {"command": "ohmai check wire-ends", "changed": "", "failures": [failure.as_dict()]},
    ]}
    if corruption == "not-json":
        state = "not json"
    elif corruption == "not-object":
        state = []
    elif corruption == "missing-signature":
        del state["signature"]
    elif corruption == "bad-signature":
        state["signature"] = 7
    elif corruption == "missing-attempts":
        del state["attempts"]
    elif corruption == "bad-attempts":
        state["attempts"] = {}
    elif corruption == "empty-attempts":
        state["attempts"] = []
    elif corruption == "empty-records":
        state["attempts"] = [{}, {}, {}]
    elif corruption == "integer-records":
        state["attempts"] = [1, 2, 3]
    elif corruption == "too-many":
        state["attempts"] *= 4
    elif corruption == "cleared-with-attempts":
        state["signature"] = None
    else:
        kind, field = corruption.split("-", 1)
        target = state["attempts"][0]
        if field not in {"command", "changed", "failures", "failure"}:
            target = target["failures"][0]
        if kind == "missing":
            del target[field]
        elif kind == "bad":
            target[field] = 7
        elif corruption == "empty-failures":
            target["failures"] = []
        elif corruption == "integer-failure":
            target["failures"] = [7]
        elif corruption == "unknown-status":
            target["status"] = "INVALID"
        elif corruption == "integer-detail":
            state["attempts"][0]["failures"][0]["details"] = [7]
    evidence = state if corruption == "not-json" else json.dumps(state)
    path = project.out / "check-attempts.json"
    path.write_text(evidence)
    if outcome == "changed":
        failure.summary = "different failure"
    elif outcome == "pass":
        failure.status = PASS
    monkeypatch.setattr(ohmai.checks, "run", lambda p, name, opts: failure)
    assert main(["check", "wire-ends", "--json"]) == 3
    output = capsys.readouterr()
    assert "STOP: corrupt attempt history (exit 3)" in output.err
    assert "preserve evidence and repair history" in output.err
    assert json.loads(output.out) == [{**failure.as_dict(), "gate": False}]
    assert path.read_text() == evidence
    assert not path.with_suffix(".tmp").exists()


def test_check_help_documents_both_stop_reasons(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["check", "--help"])
    assert exc.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "3 stop after third identical failure signature or corrupt attempt history" in help_text
