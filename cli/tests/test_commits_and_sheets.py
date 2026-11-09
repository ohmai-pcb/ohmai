# SPDX-License-Identifier: Apache-2.0
"""Commit trailers (check commits, hook, ohmai commit, review authors) and phase-7 schematic checks."""

from __future__ import annotations

import shutil
import subprocess

import pytest
import yaml
from helpers import good_tps63001, netlist

from ohmai import trailers as tr
from ohmai.checks import run
from ohmai.checks.readability import analyze
from ohmai.cli import main
from ohmai.project import find_project

HAS_GIT = shutil.which("git") is not None
GOOD = "name=OhmAI; kind=agent; vendor=V; model=M; instructions=ohmai skill"
ID = ["-c", "user.name=t", "-c", "user.email=t@t"]
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git missing")


def git(root, *a, check=True):
    return subprocess.run(["git", "-C", str(root), *ID, *a], capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """`ohmai new` with git: main has the initial commit, we are on phase/requirements, hook installed."""
    d = tmp_path / "demo"
    assert main(["new", str(d), "--idea", "x"]) == 0
    monkeypatch.chdir(d)
    monkeypatch.delenv("CI", raising=False)
    return find_project(d)


def commit(root, msg, name="f.txt"):
    (root / name).write_text((root / name).read_text() + "x\n" if (root / name).exists() else "x\n")
    git(root, "add", "-A")
    return git(root, "commit", "-q", "-m", msg, check=False)


def test_validate_trailer_values():
    assert tr.validate(GOOD) == []
    assert tr.validate("name=Alex; kind=human") == []
    assert any("kind" in e for e in tr.validate("name=x; kind=robot; vendor=v; model=m; instructions=i"))
    assert any("model" in e for e in tr.validate("name=x; kind=agent; vendor=v; instructions=i"))
    assert tr.validate("OhmAI (free form)")
    assert tr.message_trailers(f"subject\n\nbody\n\nOhmAI-Author: {GOOD}\n") == [GOOD]
    assert tr.message_trailers(f"subject\n\nOhmAI-Author: {GOOD}\n\nmore body\n") == []   # not the trailer block


@needs_git
def test_new_installs_hook_and_initial_commit_has_trailer(repo):
    ok, msg = tr.hook_status(repo.root)
    assert ok, msg
    log = git(repo.root, "log", "-1", "main", "--format=%(trailers:key=OhmAI-Author,valueonly)").stdout.strip()
    assert log and tr.validate(log) == []


@needs_git
def test_hook_rejects_missing_or_malformed_trailer(repo):
    assert commit(repo.root, "no trailer").returncode != 0
    assert commit(repo.root, "bad\n\nOhmAI-Author: name=x; kind=robot").returncode != 0
    assert commit(repo.root, "agent no model\n\nOhmAI-Author: name=x; kind=agent; vendor=v; instructions=i").returncode != 0
    assert commit(repo.root, f"good\n\nOhmAI-Author: {GOOD}").returncode == 0
    assert commit(repo.root, "owner\n\nOhmAI-Author: name=Alex; kind=human").returncode == 0


@needs_git
def test_check_commits_fail_warn_pass(repo):
    root = repo.root
    assert run(repo, "commits", {}).status == "PASS"          # nothing since main
    git(root, "commit", "-q", "--allow-empty", "--no-verify", "-m", "legacy commit")
    r = run(repo, "commits", {})
    assert r.status == "FAIL" and any("legacy commit" in d for d in r.details), r.details
    # owner decision: grandfather history up to here; no rewrite
    cfg = yaml.safe_load((root / "ohmai.yaml").read_text())
    cfg.setdefault("commits", {})["trailer_required_since"] = git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "ohmai.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    git(root, "commit", "-q", "-am", f"set cutoff\n\nOhmAI-Author: {GOOD}")
    repo = find_project(root)
    r = run(repo, "commits", {})
    assert r.status == "WARN" and "1 grandfathered" in r.summary, (r.summary, r.details)
    git(root, "commit", "-q", "--allow-empty", "--no-verify", "-m", "after cutoff")
    assert run(repo, "commits", {}).status == "FAIL"
    # the review gate fails on it too
    r = run(repo, "review:constraints", {})
    assert any(d.startswith("commits:") for d in r.details)


@needs_git
def test_check_commits_bad_cutoff_ref(repo):
    cfg = yaml.safe_load((repo.root / "ohmai.yaml").read_text())
    cfg["commits"] = {"trailer_required_since": "nope"}
    (repo.root / "ohmai.yaml").write_text(yaml.safe_dump(cfg))
    git(repo.root, "commit", "-q", "-am", f"x\n\nOhmAI-Author: {GOOD}")
    assert run(find_project(repo.root), "commits", {}).status == "FAIL"


@needs_git
def test_ohmai_commit_appends_trailer_from_env(repo, monkeypatch):
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(k, "t")
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(k, "t@t")
    (repo.root / "n.txt").write_text("n\n")
    git(repo.root, "add", "n.txt")
    assert main(["commit", "-m", "no identity"]) == 1
    for f, v in zip(tr.FIELDS, ["Bot", "agent", "V", "M", "skill"], strict=False):
        monkeypatch.setenv(tr.ENV[f], v)
    assert main(["commit", "-m", "with env"]) == 0
    val = git(repo.root, "log", "-1", "--format=%(trailers:key=OhmAI-Author,valueonly)").stdout.strip()
    assert val == "name=Bot; kind=agent; vendor=V; model=M; instructions=skill"
    assert run(repo, "commits", {}).status == "PASS"


@needs_git
def test_review_authors_from_trailers_status_only_for_precutoff(repo):
    from ohmai.review import authors

    root = repo.root
    st = (root / "STATUS.md").read_text()
    st = st.replace('{name: "", kind: agent,', '{name: "StatusAuthor", kind: agent,', 1)
    assert "StatusAuthor" in st
    (root / "STATUS.md").write_text(st)
    commit(root, f"work\n\nOhmAI-Author: {GOOD}", "w.txt")
    cfg = yaml.safe_load((root / "ohmai.yaml").read_text())
    cfg["commits"] = {"trailer_required_since": git(root, "rev-parse", "main").stdout.strip()}
    (root / "ohmai.yaml").write_text(yaml.safe_dump(cfg))
    p = find_project(root)
    names = [a.get("name") for a in authors(p, [root / "w.txt"])]
    assert names == ["OhmAI"], names               # trailers only: all commits on w.txt are post-cutoff
    git(root, "commit", "-q", "--allow-empty", "-m", f"cut\n\nOhmAI-Author: {GOOD}")
    (root / "old.txt").write_text("o\n")
    git(root, "add", "old.txt")
    git(root, "commit", "-q", "--no-verify", "-m", "legacy, no trailer")
    cfg["commits"]["trailer_required_since"] = git(root, "rev-parse", "HEAD").stdout.strip()
    (root / "ohmai.yaml").write_text(yaml.safe_dump(cfg))
    names = [a.get("name") for a in authors(find_project(root), [root / "old.txt"])]
    assert "StatusAuthor" in names                 # pre-cutoff commit without trailer: STATUS fallback


@needs_git
def test_hooks_cli(repo, capsys):
    hook = tr.hooks_dir(repo.root) / "commit-msg"
    hook.unlink()
    assert main(["hooks", "status"]) == 1
    assert main(["hooks", "install"]) == 0 and main(["hooks", "status"]) == 0
    hook.write_text("#!/bin/sh\nexit 0\n")
    assert main(["hooks", "install"]) == 1          # foreign hook kept unless --force
    assert main(["hooks", "install", "--force"]) == 0


def test_phases_run_commits_before_review():
    from ohmai.phases import PHASES

    for ph in PHASES:
        assert "commits" in ph.checks
        revs = [i for i, c in enumerate(ph.checks) if c.startswith("review:")]
        assert all(i > ph.checks.index("commits") for i in revs)


# ---------- phase 7: symbol maps, auto-named nets, readability ----------

@pytest.fixture
def proj(tmp_path, monkeypatch):
    d = tmp_path / "demo"
    assert main(["new", str(d), "--idea", "x", "--no-git"]) == 0
    monkeypatch.chdir(d)
    shutil.copy(d / "parts/constraints/TPS63001.example.yaml", d / "parts/constraints/TPS63001.yaml")
    return find_project(d)


def _nl(proj, comps, nets, lib):
    p = proj.root / "t.net"
    p.write_text(netlist(comps, nets, lib))
    return str(p)


def test_symbol_map_translates_and_detects_stale(proj):
    comps, nets, lib = good_tps63001()
    lib["TPS63001"] = {**lib["TPS63001"], "20": "VDD"}
    nl = _nl(proj, comps, nets, lib)
    assert run(proj, "pin-map", {"netlist": nl}).status == "FAIL"
    maps = proj.root / "parts/symbol-maps"
    maps.mkdir(parents=True, exist_ok=True)
    (maps / "TPS63001.yaml").write_text('pins:\n  "20": {datasheet: "20", symbol_name: VDD, cite: {page: 1}}\n')
    assert run(proj, "pin-map", {"netlist": nl}).status in ("PASS", "WARN")
    (maps / "TPS63001.yaml").write_text('pins:\n  "20": {datasheet: "20", symbol_name: VIN_OLD}\n')
    r = run(proj, "pin-map", {"netlist": nl})
    assert r.status == "FAIL" and any("map stale" in d for d in r.details)


def test_netlist_accepts_kicad_auto_named_wired_net(proj):
    (proj.root / "expected_nets.csv").write_text("net,nodes\nFB,U1.4,R2.2,R3.1\n")
    comps, nets, lib = good_tps63001()
    nets["Net-(U1-FB)"] = nets.pop("/FB")
    r = run(proj, "netlist", {"netlist": _nl(proj, comps, nets, lib)})
    assert r.status in ("PASS", "WARN") and not any("Net-(U1-FB)" in d for d in r.details), r.details
    nets["/OTHER"] = nets.pop("Net-(U1-FB)")
    r = run(proj, "netlist", {"netlist": _nl(proj, comps, nets, lib)})
    assert r.status == "FAIL" and any(d.startswith("name:") for d in r.details)


def _sheet(body: str) -> str:
    return ('(kicad_sch (version 20250114) (generator "t") (uuid "u") (paper "A4") (lib_symbols)\n'
            + body + "\n)")


def test_readability_flags_off_grid_and_local_labels():
    fails, _ = analyze(_sheet('(wire (pts (xy 25.4 25.4) (xy 50.8 25.4)) (uuid "w1"))'))
    assert fails == [], fails
    fails, _ = analyze(_sheet('(wire (pts (xy 25 25.4) (xy 50.8 25.4)) (uuid "w1"))'))
    assert any("grid" in f for f in fails), fails
    lab = '(label "X" (at 25.4 25.4 0) (effects (font (size 1.27 1.27))) (uuid "l1"))'
    fails, _ = analyze(_sheet('(wire (pts (xy 25.4 25.4) (xy 50.8 25.4)) (uuid "w1"))\n' + lab))
    assert any("label" in f.lower() for f in fails), fails
    fails, _ = analyze(_sheet('(wire (pts (xy 25.4 25.4) (xy 50.8 25.4)) (uuid "w1"))\n' + lab), allow_labels=["X"])
    assert not any("local label" in f.lower() for f in fails), fails


# ---------- passives semantics (FICTIONAL test part "TST": not real datasheet data) ----------

TST = """# FICTIONAL part for tests only.
schema: ohmai-constraints/v1
part: TST
manufacturer: Fictional
match: {lib_symbol: TST}
datasheet: {file: datasheets/TST.pdf}
review: {status: draft}
pins:
  - {number: "1", name: VDD, direction: supply_in, cite: {page: 1},
     required_external: [{kind: capacitor, to: GND, nominal: 100, unit: nF, cite: {page: 1}}]}
  - {number: "2", name: VDD, direction: supply_in, cite: {page: 1},
     required_external: [{kind: capacitor, to: GND, nominal: 100, unit: nF, cite: {page: 1}}]}
  - {number: "3", name: IN, direction: input, cite: {page: 2},
     required_external: [{kind: resistor, nominal: 1, unit: kohm, cite: {page: 2}},
                         {kind: capacitor, to: GND, min: 10, unit: nF, cite: {page: 2}}]}
  - {number: "4", name: GND, direction: ground, cite: {page: 1}}
"""


def _tst(proj, caps, filt_cap_on="/INF", r_value="1k"):
    (proj.root / "parts/constraints/TPS63001.yaml").unlink(missing_ok=True)
    (proj.root / "parts/constraints/TST.yaml").write_text(TST)
    comps = {"U1": ("TST", "TST"), "R1": (r_value, "R"), "C9": ("22n", "C")}
    nets = {"+3V3": ["U1.1", "U1.2"], "GND": ["U1.4", "C9.2"], "/IN": ["U1.3", "R1.1"], "/INF": ["R1.2"]}
    nets[filt_cap_on].append("C9.1")
    for i, v in enumerate(caps, start=1):
        comps[f"C{i}"] = (v, "C")
        nets["+3V3"].append(f"C{i}.1")
        nets["GND"].append(f"C{i}.2")
    lib = {"TST": {"1": "VDD", "2": "VDD", "3": "IN", "4": "GND"}, "C": {"1": "~", "2": "~"},
           "R": {"1": "~", "2": "~"}}
    return run(proj, "passives", {"netlist": _nl(proj, comps, nets, lib)})


def test_passives_rail_decoupling_needs_one_cap_per_supply_pin(proj):
    r = _tst(proj, ["100n"])
    assert r.status == "FAIL" and any("no free capacitor" in d for d in r.details), r.details
    assert _tst(proj, ["100n", "100n"]).status == "PASS"


def test_passives_nominal_cap_is_a_minimum_not_a_maximum(proj):
    assert _tst(proj, ["1u", "220n"]).status == "PASS"          # bigger than nominal: fine
    assert _tst(proj, ["10n", "100n"]).status == "FAIL"         # smaller: not


def test_passives_look_through_series_resistor(proj):
    assert _tst(proj, ["100n", "100n"], "/INF").status == "PASS"   # filter cap after the series R
    assert _tst(proj, ["100n", "100n"], "/IN").status == "PASS"    # or on the pin itself
    assert _tst(proj, ["100n", "100n"], r_value="10k").status == "FAIL"   # nominal 1k: ±5 %


# ---------- readability: text placement rules added after reference-board Reviewer-A F1/F13/F14/F15 ----------

PWR_LIB = ('(symbol "power:VCC" (power) (property "Reference" "#PWR" (at 0 0 0)) (property "Value" "VCC" (at 0 -3.81 0))'
           ' (symbol "VCC_1_1" (pin power_in line (at 0 0 90) (length 0) (name "VCC") (number "1"))))')


def _pwr(ref, x, y, vy):
    return (f'(symbol (lib_id "power:VCC") (at {x} {y} 0) (uuid "{ref}") (property "Reference" "{ref}" (at {x} {y} 0)'
            f' (effects (hide yes))) (property "Value" "VSYS" (at {x} {vy} 0) (effects (font (size 1.27 1.27)))))')


def _sheet2(body: str, title: str = "t") -> str:
    return (f'(kicad_sch (version 20250114) (generator "t") (uuid "u") (paper "A4") (title_block (title "{title}"))'
            f' (lib_symbols {PWR_LIB})\n{body}\n)')


def test_readability_power_text_on_foreign_junction():
    w = ('(wire (pts (xy 20.32 25.4) (xy 30.48 25.4)) (uuid "w1"))(wire (pts (xy 25.4 25.4) (xy 25.4 20.32)) (uuid "w2"))'
         '(junction (at 25.4 25.4) (uuid "j1"))')
    # VSYS power symbol whose net name sits right under another node's junction (F1)
    fails, _ = analyze(_sheet2(w + _pwr("#PWR1", 25.4, 30.48, 26.67)))
    assert any("ambiguous: power symbol 'VSYS'" in f for f in fails), fails
    fails, _ = analyze(_sheet2(w + _pwr("#PWR1", 25.4, 45.72, 41.91)))
    assert not any("ambiguous" in f for f in fails), fails


def test_readability_title_overflow():
    fails, _ = analyze(_sheet2("", "short"))
    assert not any("title:" in f for f in fails)
    fails, _ = analyze(_sheet2("", "reference-board: 5 Pico edge, castellations, test pads (Pico 2 datasheet Fig. 2, §3.1)"))
    assert any("title:" in f for f in fails), fails


def test_passives_target_by_own_pin_name_on_unlabelled_net(proj):
    tst = TST.replace('to: GND, nominal: 100, unit: nF, cite: {page: 1}}]}\n  - {number: "2"',
                      'to: GND, nominal: 100, unit: nF, cite: {page: 1}}]}\n  - {number: "2"', 1)
    tst = tst.replace('''  - {number: "4", name: GND, direction: ground, cite: {page: 1}}''',
                      '''  - {number: "4", name: GND, direction: ground, cite: {page: 1}}
  - {number: "5", name: PGND, direction: ground, cite: {page: 1}}
  - {number: "6", name: VIN, direction: supply_in, cite: {page: 1},
     required_external: [{kind: capacitor, to: PGND, min: 1, unit: uF, cite: {page: 1}}]}''')
    (proj.root / "parts/constraints/TPS63001.yaml").unlink(missing_ok=True)
    (proj.root / "parts/constraints/TST.yaml").write_text(tst)
    comps = {"U1": ("TST", "TST"), "R1": ("1k", "R"), "C9": ("22n", "C"), "C1": ("100n", "C"), "C2": ("100n", "C"),
             "C5": ("4.7u", "C")}
    nets = {"+3V3": ["U1.1", "U1.2", "C1.1", "C2.1", "U1.6", "C5.1"], "GND": ["U1.4", "C9.2", "C1.2", "C2.2"],
            "/IN": ["U1.3", "R1.1", "C9.1"], "/INF": ["R1.2"], "Net-(U1-PGND)": ["U1.5", "C5.2"]}
    lib = {"TST": {"1": "VDD", "2": "VDD", "3": "IN", "4": "GND", "5": "PGND", "6": "VIN"},
           "C": {"1": "~", "2": "~"}, "R": {"1": "~", "2": "~"}}
    r = run(proj, "passives", {"netlist": _nl(proj, comps, nets, lib)})
    assert r.status == "PASS", r.details


# ---------- kicad-app: KiCad application version pin ----------

def test_version_in_range():
    from ohmai.checks.schematic import version_in_range as v
    assert v("9.0.2", ">=9.0.2,<9.1") == (True, "")
    assert v("9.0.9", ">=9.0.2 <9.1") == (True, "")
    assert v("9.0.1", ">=9.0.2,<9.1")[0] is False
    assert v("9.1.0", ">=9.0.2,<9.1")[0] is False
    assert v("8.0.8", "9.0.x")[0] is False and v("9.0.7", "9.0.x") == (True, "")
    assert v("9.0.2-1", "9.0.x") == (True, "")
    assert v("9.0.2", "~9")[1]          # unparseable spec is an error, not a pass


def test_kicad_app_check(proj):
    cfg = yaml.safe_load((proj.root / "ohmai.yaml").read_text())
    cfg.setdefault("kicad", {})["app"] = ">=9.0.2,<9.1"
    (proj.root / "ohmai.yaml").write_text(yaml.safe_dump(cfg))
    p = find_project(proj.root)
    assert run(p, "kicad-app", {"kicad_version": "9.0.2"}).status == "PASS"
    r = run(p, "kicad-app", {"kicad_version": "9.1.0"})
    assert r.status == "FAIL" and "outside the pinned range" in r.summary
    cfg["kicad"].pop("app")
    (proj.root / "ohmai.yaml").write_text(yaml.safe_dump(cfg))
    p = find_project(proj.root)
    assert run(p, "kicad-app", {"kicad_version": "9.0.2"}).status == "WARN"


@needs_git
def test_hooks_install_outside_a_project(tmp_path, monkeypatch, capsys):
    """The OhmAI repo itself has no ohmai.yaml: `ohmai hooks install` uses the git work tree root."""
    d = tmp_path / "plain"
    d.mkdir()
    git(d, "init", "-q")
    monkeypatch.chdir(d)
    monkeypatch.delenv("CI", raising=False)
    assert main(["hooks", "status"]) == 1
    assert main(["hooks", "install"]) == 0 and main(["hooks", "status"]) == 0
    (d / "f.txt").write_text("x\n")
    git(d, "add", "f.txt")
    r = git(d, "commit", "-q", "-m", "no trailer", check=False)
    assert r.returncode != 0 and "no 'OhmAI-Author:' trailer" in r.stderr     # the installed hook really runs
    assert git(d, "commit", "-q", "-m", "ok", "-m", f"OhmAI-Author: {GOOD}", check=False).returncode == 0


@needs_git
def test_doctor_warns_without_hook(tmp_path, monkeypatch, capsys):
    from ohmai import kicad

    monkeypatch.setattr(kicad, "kicad_cli", lambda: "fictional-kicad-cli")
    monkeypatch.setattr(kicad, "version", lambda: "9.0.2")
    d = tmp_path / "plain"
    d.mkdir()
    git(d, "init", "-q")
    monkeypatch.chdir(d)
    monkeypatch.delenv("CI", raising=False)
    assert main(["doctor"]) == 0                      # warnings only
    out = capsys.readouterr().out
    assert "WARN  hooks" in out and "missing" in out
    assert main(["doctor", "--strict"]) == 1
    capsys.readouterr()
    assert main(["hooks", "install"]) == 0
    capsys.readouterr()
    main(["doctor"])
    assert "PASS  hooks" in capsys.readouterr().out
    monkeypatch.setenv("CI", "1")
    (tr.hooks_dir(d) / "commit-msg").unlink()
    main(["doctor", "--strict"])
    assert "INFO  hooks" in capsys.readouterr().out   # CI clones have no hooks: not a warning there


@needs_git
def test_pre_push_preserves_lfs_gate_and_ref_stdin(repo, tmp_path, monkeypatch):
    import os

    from ohmai import trailers
    root = repo.root if hasattr(repo, 'root') else repo
    # A fake LFS executable records stdin; this test never contacts a remote.
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    capture = tmp_path / 'lfs-refs.txt'
    executable = bin_dir / 'git-lfs'
    executable.write_text('#!/bin/sh\n[ "$1" = pre-push ] || exit 9\ncat > "$LFS_TEST_CAPTURE"\n')
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', str(bin_dir) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('LFS_TEST_CAPTURE', str(capture))
    subprocess.run(['git', '-C', str(root), 'config', '--local', 'filter.lfs.process', 'git-lfs filter-process'], check=True)
    trailers.install_hook(root)
    hook = trailers.hooks_dir(root) / 'pre-push'
    result = subprocess.run(['sh', str(hook), 'origin', 'fictional-remote'], cwd=root,
                            input='fictional ref update\n', text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert capture.read_text() == 'fictional ref update\n'
