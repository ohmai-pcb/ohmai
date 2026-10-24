from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
import yaml
from helpers import good_tps63001, netlist

from ohmai.checks import FAIL, WARN, run
from ohmai.checks.bom import diff_fab, power_w, read_rows
from ohmai.checks.constraints import schema_for, validate
from ohmai.cli import main
from ohmai.netlist import parse_value
from ohmai.project import find_project
from ohmai.resources import template_dir
from ohmai.status import parse

HAS_GIT = shutil.which("git") is not None
TRAILER = "OhmAI-Author: name=OhmAI; kind=agent; vendor=V; model=M; instructions=ohmai skill"


@pytest.fixture
def proj(tmp_path, monkeypatch):
    d = tmp_path / "demo"
    assert main(["new", str(d), "--idea", "A USB-C PD power bank", "--no-git"]) == 0
    monkeypatch.chdir(d)
    return find_project(d)


def test_help(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "ohmai" in capsys.readouterr().out


def test_new_fills_placeholders(proj):
    st = (proj.root / "STATUS.md").read_text()
    assert "A USB-C PD power bank" in st and "{{" not in st
    assert (proj.root / "hardware" / "demo.kicad_sch").is_file()
    assert (proj.root / ".ohmai" / "phases" / "07-schematic.md").is_file()
    assert proj.config["hardware"]["schematic"] == "hardware/demo.kicad_sch"


@pytest.mark.skipif(not HAS_GIT, reason="git missing")
def test_new_git(tmp_path):
    d = tmp_path / "g"
    assert main(["new", str(d), "--idea", "x"]) == 0
    assert (d / ".git").is_dir()


def test_new_refuses_nonempty(tmp_path):
    (tmp_path / "f").write_text("x")
    assert main(["new", str(tmp_path), "--idea", "x"]) == 1


def test_status_parse(proj):
    st = parse((proj.root / "STATUS.md").read_text())
    assert st.phase.id == "requirements" and not st.problems
    assert len(st.open_questions) == 3 and st.markets == []


def test_status_table():
    st = parse("---\nproject: p\nohmai_version: 0\nphase: 7\ngate: open\n---\n## Locked decisions\n"
               "| ID | Date | Decision | Phase | Ref |\n|--|--|--|--|--|\n| D-001 | 2026-10-15 | Use TPS63001 | 4 | #3 |\n"
               "## Open questions\n- [x] done\n- [ ] still open\n")
    assert st.phase.id == "schematic" and st.decisions[0]["id"] == "D-001"
    assert st.open_questions == ["still open"] and st.closed_questions == ["done"]


def test_example_constraints_valid():
    data = yaml.safe_load((template_dir() / "parts/constraints/TPS63001.example.yaml").read_text())
    schema = json.loads((template_dir() / "parts/constraints.schema.json").read_text())
    assert validate(data, schema) == []


def test_constraints_reject_unknown_when_reviewed(proj):
    data = yaml.safe_load((proj.root / "parts/constraints/TPS63001.example.yaml").read_text())
    data["review"] = {"status": "reviewed", "reviewer": "owner", "date": "2026-10-15"}
    data["pins"][0]["direction"] = "unknown"
    errs = validate(data, schema_for(proj))
    assert any("pins/0/direction" in e for e in errs)
    data["pins"][0].pop("cite")
    assert any("cite" in e for e in validate(data, schema_for(proj)))


def _setup_constraints(proj):
    src = proj.root / "parts/constraints/TPS63001.example.yaml"
    shutil.copy(src, proj.root / "parts/constraints/TPS63001.yaml")


def _write(proj, comps, nets, libparts) -> str:
    p = proj.root / "test.net"
    p.write_text(netlist(comps, nets, libparts))
    return str(p)


def test_pin_checks_pass(proj):
    _setup_constraints(proj)
    nl = _write(proj, *good_tps63001())
    for name in ("pin-map", "single-source", "passives", "pullups", "i2c-addr"):
        r = run(proj, name, {"netlist": nl})
        assert r.status in ("PASS", "WARN", "SKIP"), (name, r.summary, r.details)


def test_pin_checks_catch_errors(proj):
    _setup_constraints(proj)
    comps, nets, lib = good_tps63001()
    lib["TPS63001"] = {**lib["TPS63001"], "20": "VDD"}          # wrong pin name
    comps["C1"] = ("2.2u", "C")                              # VCC cap too big
    nets["/SDA"] = ["U1.2", "C9.1"]                           # pull-up removed
    nets.pop("+3V3")
    nets["/SW1"] = ["U1.14", "C2.2", "U1.13"]                 # SW1 tied to SW2
    nets.pop("/SW2")
    comps["C9"] = ("10p", "C")
    nl = _write(proj, comps, nets, lib)
    assert run(proj, "pin-map", {"netlist": nl}).status == "FAIL"
    assert run(proj, "passives", {"netlist": nl}).status == "FAIL"
    assert run(proj, "pullups", {"netlist": nl}).status == "FAIL"
    assert run(proj, "single-source", {"netlist": nl}).status == "FAIL"


def test_netlist_diff(proj):
    (proj.root / "expected_nets.csv").write_text("net,nodes\nVCC,U1.10,C1.*\nGND,C1.*,U1.11,U1.12,U1.15,...\n")
    nl = _write(proj, *good_tps63001())
    r = run(proj, "netlist", {"netlist": nl})
    assert r.status == "PASS", r.details
    (proj.root / "expected_nets.csv").write_text("net,nodes\nVCC,U1.10,U1.20\n")
    r = run(proj, "netlist", {"netlist": nl})
    assert r.status == "FAIL" and any("split" in d or "extra" in d for d in r.details)


def test_values():
    assert parse_value("100n") == pytest.approx(1e-7)
    assert parse_value("4k7") == pytest.approx(4700)
    assert parse_value("0.22uF") == pytest.approx(2.2e-7)
    assert power_w("RES 10k 0.2W 0603") == pytest.approx(0.2)
    assert power_w("250 mW") == pytest.approx(0.25)
    assert power_w("1/4W") == pytest.approx(0.25)


def test_bom_fab_diff_catches_substitution():
    _, bom = read_rows(
        "refs,qty,value,footprint,manufacturer,mpn,approved_alternates,status,dnp,rating_notes\n"
        "U1,1,MP2651,QFN,MPS,MP2651GVT-P,,locked,,\n"
        "R1,1,10k,0603,Yageo,RC0603FR-0710KL,,locked,,250 mW\n")
    fh, fab = read_rows("Designator,Actual MPN,Description\nU1,MP2651GVT-Z,charger\nR1,XYZ-10K,RES 10k 0.2W 0603\n")
    errors, _ = diff_fab(bom, fh, fab)
    assert any("substituted MP2651GVT-Z" in e for e in errors)
    assert any("200 mW" in e for e in errors)


def test_bom_lock(proj):
    (proj.root / "bom/bom.csv").write_text(
        "refs,qty,value,footprint,manufacturer,mpn,approved_alternates,status,dnp\nR1 R2,2,10k,0603,Y,RC1,RC2,approved,\n")
    r = run(proj, "bom-lock", {})
    assert r.status == "FAIL"


def test_questions_warn_until_answered(proj):
    assert run(proj, "questions", {}).status == "WARN"  # template ships three open questions
    st = proj.root / "STATUS.md"
    text = st.read_text().replace("- [ ] ", "- [x] ", 2).replace("markets: []", "markets: [EU]")
    text = text.replace("- [ ] ", "- [ ] Non-blocking: ", 1)
    st.write_text(text)
    assert run(proj, "questions", {}).status == "PASS"


def _answer_all(st):
    text = st.read_text().replace("- [ ] ", "- [x] ")
    st.write_text(text.replace("markets: []", "markets: [EU]"))


def test_questions_warn_without_markets(proj):
    st = proj.root / "STATUS.md"
    st.write_text(st.read_text().replace("- [ ] ", "- [x] "))
    r = run(proj, "questions", {})
    assert r.status == "WARN" and any("target markets" in d for d in r.details)
    _answer_all(st)
    assert run(proj, "questions", {}).status == "PASS"


def test_status_parses_markets_and_warnings(proj):
    from ohmai.status import load

    st = proj.root / "STATUS.md"
    text = st.read_text().replace("markets: []", "markets: [eu, UK]")
    text = text.replace("## Manual warnings\n", "## Manual warnings\n- MW-01: grounded probe limit (phase 2)\n")
    st.write_text(text)
    s = load(st)
    assert s.markets == ["EU", "UK"] and s.manual_warnings == {"MW-01": "grounded probe limit (phase 2)"}


def test_manual_warnings_link(proj):
    arch = proj.root / "docs/architecture.md"
    arch.write_text(arch.read_text() + "\nLimit goes to the manual as MW-01.\n")
    r = run(proj, "manual-warnings", {})
    assert r.status == "FAIL" and "MW-01" in r.details[0]
    st = proj.root / "STATUS.md"
    st.write_text(st.read_text().replace("## Manual warnings\n", "## Manual warnings\n- MW-01: limit\n"))
    assert run(proj, "manual-warnings", {}).status == "PASS"


GOOD_MANUAL = """# User instructions
## Product identification
Type PK-1, serial number on the bottom label.
## Manufacturer and contact
Acme AB, Storgatan 1, 111 22 Stockholm, Sweden, info@example.com. The manufacturer is the economic operator established in the EU.
## Intended use
Bench thermometer.
## Ratings and specifications
5 V USB, 100 mA.
## Safety information
- MW-01: WARNING: do not use a grounded probe on earthed objects.
## Installation and use
Solder it.
## Maintenance and cleaning
None.
## Disposal
Crossed-out wheeled bin: separate collection.
## Conformity
CE. The EU declaration of conformity is at https://example.com/doc.
## Language versions
English, Swedish (language of the market).
"""


def test_manual_check(proj):
    assert run(proj, "manual", {}).status == "FAIL"  # template marker
    st = proj.root / "STATUS.md"
    _answer_all(st)
    st.write_text(st.read_text().replace("## Manual warnings\n", "## Manual warnings\n- MW-01: limit\n"))
    man = proj.root / "docs/manual.md"
    man.write_text(GOOD_MANUAL)
    r = run(proj, "manual", {})
    assert r.status == "PASS", r.details
    man.write_text(GOOD_MANUAL.replace("- MW-01: ", "- ").replace("wheeled bin", "bin"))
    r = run(proj, "manual", {})
    assert r.status == "FAIL"
    assert any("MW-01" in d for d in r.details) and any("WEEE" in d for d in r.details)
    st.write_text(st.read_text().replace("markets: [EU]", "markets: [EU, JP]"))
    man.write_text(GOOD_MANUAL)
    assert run(proj, "manual", {}).status == "WARN"


def test_upgrade_guides(proj):
    from ohmai.upgrade import upgrade_guides

    assert upgrade_guides(proj) == [f"already pinned to OhmAI {__import__('ohmai').__version__}"]
    g = proj.root / ".ohmai/phases/03-safety.md"
    g.write_text("old rules\n")
    (proj.root / "docs/manual.md").unlink()
    st = proj.root / "STATUS.md"
    st.write_text(st.read_text().replace('ohmai_version: "', 'ohmai_version: "0.0.1-', 1))
    log = upgrade_guides(proj, dry_run=True, add_missing=True)
    assert any("03-safety.md" in x for x in log) and any("docs/manual.md" in x for x in log)
    assert g.read_text() == "old rules\n"
    upgrade_guides(proj, add_missing=True)
    assert "old rules" not in g.read_text() and (proj.root / "docs/manual.md").is_file()
    assert "0.0.1-" not in st.read_text()
    assert "{{project_name}}" not in (proj.root / "docs/manual.md").read_text()


def test_rfq(proj):
    (proj.root / "rfq/acme-transformer.md").write_text("# RFQ\nSee D-004 and STATUS.md\n")
    assert run(proj, "rfq", {}).status == "FAIL"


def test_check_list(capsys):
    assert main(["check", "--list"]) == 0
    assert "schematic" in capsys.readouterr().out


def test_skill_install(tmp_path):
    assert main(["skill", "install", "--dest", str(tmp_path / "s")]) == 0
    assert (tmp_path / "s" / "SKILL.md").is_file()
    assert (Path(tmp_path / "s" / "phases")).is_dir()


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli missing")
def test_sheet_load_catches_broken_sheet(proj):
    assert run(proj, "sheet-load", {}).status == "PASS"
    (proj.root / "hardware" / "broken.kicad_sch").write_text('(kicad_sch (version 20250114) (paper "A4"')
    r = run(proj, "sheet-load", {})
    assert r.status == "FAIL" and any("broken.kicad_sch" in d for d in r.details)


# ---------- independent review gate ----------

def _review_proj(proj):
    c = proj.root / "parts" / "constraints"
    shutil.copy(c / "TPS63001.example.yaml", c / "TPS63001.yaml")
    st = proj.root / "STATUS.md"
    st.write_text(re.sub(r"authors:.*?\nupdated:", "authors:\n  - {name: OhmAI, kind: agent, vendor: VendorA, model: m1, "
                         "instructions: ohmai-skill}\nupdated:", st.read_text(), count=1, flags=re.S))
    return find_project(proj.root)


def _write_review(p, name="reviewer-a", vendor="VendorB", model="m9", indep=1, h=None, rows=None, path=None):
    from ohmai.review import content_hash, gates, scope_files

    h = h or content_hash(p, scope_files(p, gates(p)["constraints"]))[0]
    rows = rows or ["| F1 | parts/constraints/TPS63001.yaml | OK | | |"]
    body = "\n".join([
        "---", f"reviewer: {name}", "kind: agent", f"vendor: {vendor}", f"model: {model}",
        "instructions: reviewer-a", f"independence: {indep}", f"files_hash: '{h}'", "date: 2026-10-15", "---",
        "| ID | File | Verdict | Finding | Cite |", "|---|---|---|---|---|", *rows, ""])
    f = path or (p.root / "reviews" / "constraints" / f"{name}.md")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body)
    return f


def test_review_start_writes_brief(proj):
    from ohmai.review import start

    p = _review_proj(proj)
    b = start(p, "constraints", today="2026-10-15")
    t = b.read_text()
    assert "files_hash: sha256:" in t and "parts/constraints/TPS63001.yaml" in t
    assert "never edit" in t and "different vendor" in t and "name=OhmAI" in t
    assert "TPS63001.example.yaml" not in t.split("## Sources")[0]
    assert (b.parent / "resolutions.md").is_file()


def test_review_check_missing_then_pass(proj):
    from ohmai.review import start

    p = _review_proj(proj)
    start(p, "constraints")
    assert run(p, "review:constraints", {}).status == FAIL
    _write_review(p)
    r = run(p, "review:constraints", {})
    assert r.status == "PASS", r.details


def test_review_same_identity_fails(proj):
    p = _review_proj(proj)
    _write_review(p, name="OhmAI", vendor="VendorA", model="m1")
    r = run(p, "review:constraints", {})
    assert r.status == FAIL and any("matches author" in d for d in r.details)


def test_review_hash_mismatch_and_owner_accept(proj):
    p = _review_proj(proj)
    _write_review(p)
    f = p.root / "parts" / "constraints" / "TPS63001.yaml"
    f.write_text(f.read_text() + "\n# edited after review\n")
    r = run(p, "review:constraints", {})
    assert r.status == FAIL and any("changed after review" in d for d in r.details)
    from ohmai.review import content_hash, gates, scope_files

    h = content_hash(p, scope_files(p, gates(p)["constraints"]))[0]
    (p.root / "reviews" / "constraints" / "resolutions.md").write_text(
        f"---\naccepted_changes: [{{files_hash: '{h}', by: owner, reason: typo only}}]\n---\n")
    assert run(p, "review:constraints", {}).status == WARN


def test_review_issue_needs_resolution(proj):
    from ohmai.review import start

    p = _review_proj(proj)
    start(p, "constraints")
    _write_review(p, rows=["| F1 | TPS63001.yaml | issue | VCC cap wrong | p.4 |"])
    res = p.root / "reviews" / "constraints" / "resolutions.md"
    r = run(p, "review:constraints", {})
    assert r.status == FAIL and any("no resolution" in d for d in r.details)
    res.write_text("| ID | Reviewer | Resolution | Ref | By | Reason |\n|---|---|---|---|---|---|\n"
                   "| F1 | reviewer-a | fixed | nothex | | |\n")
    assert any("commit hash" in d for d in run(p, "review:constraints", {}).details)
    res.write_text("| ID | Reviewer | Resolution | Ref | By | Reason |\n|---|---|---|---|---|---|\n"
                   "| F1 | reviewer-a | accepted | | owner | within margin, p.4 |\n")
    assert run(p, "review:constraints", {}).status == "PASS"


def test_review_low_independence_warns_and_extra_path(proj):
    p = _review_proj(proj)
    cfg = yaml.safe_load((p.root / "ohmai.yaml").read_text())
    cfg["reviews"]["gates"]["constraints"]["extra_paths"] = ["docs/review-x.md"]
    (p.root / "ohmai.yaml").write_text(yaml.safe_dump(cfg))
    p = find_project(p.root)
    _write_review(p, name="reviewer-a", vendor="VendorA", model="m1", indep=1, path=p.root / "docs" / "review-x.md")
    r = run(p, "review:constraints", {})
    assert r.status == WARN and any("support only 3" in d for d in r.details)
    _write_review(p, name="jo", indep=4, path=p.root / "docs" / "review-x.md")
    r = run(p, "review:constraints", {})
    assert r.status == WARN and any("less independent than this gate requires" in d for d in r.details)


def test_review_cli_and_phase_checks(proj, capsys):
    p = _review_proj(proj)
    assert main(["review", "start", "5"]) == 0
    assert (p.root / "reviews" / "constraints" / "BRIEF.md").is_file()
    assert main(["review", "check", "constraints"]) == 1
    from ohmai.phases import resolve

    assert "review:constraints" in resolve("constraints").checks
    assert run(p, "review:requirements", {}).status == "PASS"


def test_review_ignores_narrative_tables_and_duplicate_copies(proj):
    from ohmai.review import start

    p = _review_proj(proj)
    start(p, "constraints")
    f = _write_review(p)
    f.write_text(f.read_text() + "\n| Item | Verdict |\n|---|---|\n| x | **should fix** |\n")
    cfg = yaml.safe_load((p.root / "ohmai.yaml").read_text())
    cfg["reviews"]["gates"]["constraints"]["extra_paths"] = ["docs/copy.md"]
    (p.root / "ohmai.yaml").write_text(yaml.safe_dump(cfg))
    p = find_project(p.root)
    (p.root / "docs" / "copy.md").write_text(f.read_text())
    r = run(p, "review:constraints", {})
    assert r.status == "PASS", r.details
    assert r.summary.startswith("constraints: 1/1")


def test_review_post_review_nit_fix_supersedes_hash(proj):
    from ohmai.review import start

    p = _review_proj(proj)
    c = p.root / "parts" / "constraints"
    shutil.copy(c / "TPS63001.yaml", c / "OTHER.yaml")
    start(p, "constraints")
    _write_review(p, rows=["| R1 | parts/constraints/TPS63001.yaml | issue | [nit] cite says §5.x, is §5.2 | p.369 |",
                           "| R2 | parts/constraints/OTHER.yaml | OK | | |",
                           "| R3 | parts/constraints/OTHER.yaml | issue | wrong value | p.4 |"])
    res = p.root / "reviews" / "constraints" / "resolutions.md"
    hdr = "| ID | Reviewer | Resolution | Ref | By | Reason |\n|---|---|---|---|---|---|\n"
    res.write_text(hdr + "| R1 | reviewer-a | fixed-post-review | abc1234 | author | cite fixed |\n"
                   "| R3 | reviewer-a | accepted | | owner | within margin |\n")
    f = c / "TPS63001.yaml"
    f.write_text(f.read_text() + "\n# cite fixed after review\n")
    r = run(p, "review:constraints", {})
    assert r.status == WARN and any("reviewer-requested nit" in d for d in r.details), r.details
    # survives a regenerated brief (per-file hashes of the reviewed brief are kept in history)
    start(p, "constraints")
    assert run(p, "review:constraints", {}).status == WARN
    # a change to a file that is not in the reviewer's nit list still needs a re-verification
    o = c / "OTHER.yaml"
    o.write_text(o.read_text() + "\n# edit\n")
    r = run(p, "review:constraints", {})
    assert r.status == FAIL and any("not covered" in d and "OTHER.yaml" in d for d in r.details)
    o.write_text(o.read_text().replace("\n# edit\n", ""))
    # only for the reviewer's own [nit] issues
    res.write_text(hdr + "| R1 | reviewer-a | fixed-post-review | abc1234 | author | cite fixed |\n"
                   "| R3 | reviewer-a | fixed-post-review | abc1234 | author | |\n")
    r = run(p, "review:constraints", {})
    assert r.status == FAIL and any("only for the reviewer's own [nit]" in d for d in r.details)


def test_review_brief_changed_since_last_reviewed_and_author_dedupe(proj):
    import subprocess

    from ohmai.review import authors, start

    p = _review_proj(proj)
    c = p.root / "parts" / "constraints"
    shutil.copy(c / "TPS63001.yaml", c / "OTHER.yaml")
    start(p, "constraints")
    _write_review(p, rows=["| R1 | parts/constraints/TPS63001.yaml | OK | | |",
                           "| R2 | parts/constraints/OTHER.yaml | OK | | |"])
    # two edits, each followed by a regenerated brief: the list must be relative to the reviewed brief
    (c / "TPS63001.yaml").write_text((c / "TPS63001.yaml").read_text() + "\n# edit 1\n")
    start(p, "constraints")
    (c / "OTHER.yaml").write_text((c / "OTHER.yaml").read_text() + "\n# edit 2\n")
    t = start(p, "constraints").read_text()
    sec = t.split("### Changed since", 1)[1].split("##", 1)[0]
    assert "last reviewed by reviewer-a" in sec
    assert "TPS63001.yaml" in sec and "OTHER.yaml" in sec
    # a free-form trailer with the same name as a full STATUS identity is not a second author
    subprocess.run(["git", "init", "-q", str(p.root)], check=True)
    subprocess.run(["git", "-C", str(p.root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(p.root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
                    "x\n\nOhmAI-Author: OhmAI (VendorA, ohmai skill)"], check=True)
    names = [a.get("name") for a in authors(find_project(p.root))]
    assert names.count("OhmAI") == 1 and "OhmAI (VendorA, ohmai skill)" not in names


def test_review_metadata_only_change_keeps_review_valid(proj):
    import subprocess

    from ohmai.review import start

    p = _review_proj(proj)
    git = ["git", "-C", str(p.root)]
    subprocess.run(["git", "init", "-q", str(p.root)], check=True)
    start(p, "constraints")
    _write_review(p)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "reviewed state\n\n" + TRAILER], check=True)
    f = p.root / "parts" / "constraints" / "TPS63001.yaml"
    d = yaml.safe_load(f.read_text())
    d["review"] = {"status": "reviewed", "reviewer": "reviewer-a", "date": "2026-10-15"}
    f.write_text(yaml.safe_dump(d, sort_keys=False))
    r = run(p, "review:constraints", {})
    assert r.status == WARN and any("only in review metadata" in x for x in r.details), r.details
    d["notes_extra"] = 1
    f.write_text(yaml.safe_dump(d, sort_keys=False))
    assert run(p, "review:constraints", {}).status == FAIL


def test_review_require_kind_human(proj):
    p = _review_proj(proj)
    cfg = yaml.safe_load((p.root / "ohmai.yaml").read_text())
    cfg["reviews"]["gates"]["constraints"]["require_kind"] = "human"
    (p.root / "ohmai.yaml").write_text(yaml.safe_dump(cfg))
    p = find_project(p.root)
    _write_review(p)                                   # agent reviewer, valid otherwise
    r = run(p, "review:constraints", {})
    assert r.status == FAIL and any("no valid review from a kind=human reviewer" in d for d in r.details)
    f = _write_review(p, name="alex", vendor="n/a", model="n/a", indep=4)
    f.write_text(f.read_text().replace("kind: agent", "kind: human"))
    r = run(p, "review:constraints", {})
    assert r.status != FAIL, r.details                 # owner/human who is not an author: passes (level WARN only)
    f.write_text(f.read_text().replace("files_hash: 'sha256:", "files_hash: 'sha256:0"))
    assert run(p, "review:constraints", {}).status == FAIL   # stale human review does not count
    f.unlink()
    f = _write_review(p, name="OhmAI", vendor="n/a", model="n/a", indep=4)   # human sharing an author's name
    f.write_text(f.read_text().replace("kind: agent", "kind: human"))
    assert any("no valid review from a kind=human" in d for d in run(p, "review:constraints", {}).details)


def test_review_require_kind_default_bringup_and_brief():
    from ohmai.review import DEFAULT_GATES

    assert DEFAULT_GATES["bringup"]["require_kind"] == "human"
    assert "require_kind" not in DEFAULT_GATES["safety"]


W_ALT = "alternatives_tried: ['level 1 other vendor: not set up', 'level 2 other model: owner declined']"


def _waiver(gate, h, extra=W_ALT, who="Reviewer-A", level=3, by="owner"):
    return (f"---\naccepted_independence: [{{gate: {gate}, reviewer: {who}, level: {level}, files_hash: '{h}', "
            f"by: {by}, owner: Alex, reason: owner decision, ref: D-016" + (f", {extra}" if extra else "") + "}]\n---\n")


def test_review_owner_independence_waiver(proj):
    p = _review_proj(proj)
    cfg = yaml.safe_load((p.root / "ohmai.yaml").read_text())
    cfg["reviews"]["gates"]["constraints"]["min_independence"] = 2
    (p.root / "ohmai.yaml").write_text(yaml.safe_dump(cfg))
    p = find_project(p.root)
    from ohmai.review import content_hash, gates, scope_files, start

    start(p, "constraints")
    _write_review(p, name="reviewer-a", vendor="VendorA", model="m1", indep=3)
    res = p.root / "reviews" / "constraints" / "resolutions.md"
    h = content_hash(p, scope_files(p, gates(p)["constraints"]))[0]
    assert run(p, "review:constraints", {}).status == WARN                       # no waiver
    res.write_text(_waiver("constraints", h, by="agent"))
    assert run(p, "review:constraints", {}).status == FAIL                       # not by the owner
    res.write_text(_waiver("constraints", h, level=2))
    assert run(p, "review:constraints", {}).status == WARN                       # other level: not this review
    res.write_text(_waiver("constraints", h, extra=""))
    r = run(p, "review:constraints", {})
    assert r.status == FAIL and any("alternatives_tried" in d for d in r.details), r.details
    res.write_text(_waiver("constraints", h))
    r = run(p, "review:constraints", {})
    assert r.status == "PASS", r.details
    assert any("WAIVED by owner Alex (owner decision, D-016); alternatives tried: level 1" in d for d in r.details)
    # changed files: the waiver does not carry over to the new hash
    f = p.root / "parts" / "constraints" / "TPS63001.yaml"
    f.write_text(f.read_text() + "\n# edited\n")
    _write_review(p, name="reviewer-a", vendor="VendorA", model="m1", indep=3)        # re-review on the new hash
    r = run(p, "review:constraints", {})
    assert r.status == WARN and not any("WAIVED" in d for d in r.details), r.details


def test_review_waiver_is_per_gate(proj):
    """A waiver in one gate's resolutions.md never applies to another gate's review."""
    p = _review_proj(proj)
    cfg = yaml.safe_load((p.root / "ohmai.yaml").read_text())
    for g in ("constraints", "bom"):
        cfg["reviews"]["gates"].setdefault(g, {})["min_independence"] = 2
    cfg["reviews"]["gates"]["bom"].update({"required": 1, "scope": ["parts/constraints/*.yaml"],
                                          "exclude": ["*.example.*"]})
    (p.root / "ohmai.yaml").write_text(yaml.safe_dump(cfg))
    p = find_project(p.root)
    from ohmai.review import content_hash, gates, scope_files, start

    h = content_hash(p, scope_files(p, gates(p)["constraints"]))[0]
    for g in ("constraints", "bom"):
        start(p, g)
        _write_review(p, name="reviewer-a", vendor="VendorA", model="m1", indep=3, path=p.root / "reviews" / g / "reviewer-a.md")
    (p.root / "reviews" / "constraints" / "resolutions.md").write_text(_waiver("constraints", h))
    assert run(p, "review:constraints", {}).status == "PASS"
    assert run(p, "review:bom", {}).status == WARN
    # a waiver naming another gate does not apply even if copied into this gate's file
    (p.root / "reviews" / "bom" / "resolutions.md").write_text(_waiver("constraints", h))
    assert run(p, "review:bom", {}).status == WARN


def test_footprints_pin_pad(proj, tmp_path):
    hw = proj.schematic.parent
    lib = hw / "demo.pretty"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "R2.kicad_mod").write_text('(footprint "R2" (descr "demo land pattern, datasheet p.3")\n'
                                      ' (pad "1" smd rect (at 0 0) (size 1 1))\n (pad "2" smd rect (at 2 0) (size 1 1))\n'
                                      ' (pad "" np_thru_hole circle (at 1 1) (size 1 1)))\n')
    (lib / "R1.kicad_mod").write_text('(footprint "R1" (descr "no cite")\n (pad "1" smd rect (at 0 0) (size 1 1)))\n')
    (hw / "fp-lib-table").write_text('(fp_lib_table (version 7)\n'
                                     '  (lib (name "demo")(type "KiCad")(uri "${KIPRJMOD}/demo.pretty")(options "")(descr ""))\n)\n')

    def nl(fp_r1, fp_r2):
        p = tmp_path / "fp.net"
        p.write_text('(export (version "E") (components'
                     f' (comp (ref "R1") (value "1k") (footprint "{fp_r1}") (libsource (lib "demo") (part "R")))'
                     f' (comp (ref "R2") (value "1k") (footprint "{fp_r2}") (libsource (lib "demo") (part "R"))))'
                     ' (libparts (libpart (lib "demo") (part "R") (pins (pin (num "1") (name "~") (type "passive"))'
                     ' (pin (num "2") (name "~") (type "passive")))))'
                     ' (nets (net (code "1") (name "A") (node (ref "R1") (pin "1")) (node (ref "R2") (pin "1")))))')
        return str(p)

    assert run(proj, "footprints", {"netlist": nl("demo:R2", "demo:R2")}).status == "PASS"
    r = run(proj, "footprints", {"netlist": nl("demo:R1", "demo:Nope")})
    assert r.status == "FAIL"
    text = "\n".join(r.details)
    assert "R1 (demo:R1): symbol pin(s) without a pad: 2" in text
    assert "without a datasheet page cite" in text
    assert "R2: footprint demo:Nope not found" in text
    assert "no footprint" in "\n".join(run(proj, "footprints", {"netlist": nl("", "demo:R2")}).details)
    (lib / "R3.kicad_mod").write_text('(footprint "R3" (descr "demo, datasheet p.3")\n (pad "1" smd rect (at 0 0) (size 1 1))\n'
                                      ' (pad "2" smd rect (at 2 0) (size 1 1))\n (pad "3" smd rect (at 4 0) (size 1 1)))\n')
    w = run(proj, "footprints", {"netlist": nl("demo:R2", "demo:R3")})   # extra pad: WARN (was a NameError)
    assert w.status == "WARN" and "pad(s) not on the symbol (unconnected copper): 3" in "\n".join(w.details)
