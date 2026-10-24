"""`ohmai upgrade`: a tier-1 pin moves only after the suites pass or their diffs are explained."""

from __future__ import annotations

import subprocess

import pytest
import yaml
from test_toolchain import fake, locked, proj  # noqa: F401 (fixture)

from ohmai import toolchain as tc
from ohmai import toolupgrade as tu
from ohmai.project import find_project

KB_OK = [{"case": "erc-x", "check": "erc", "caught": True, "skipped": False}]
GOLD_OK = [{"golden": "ldo-led", "status": "PASS", "diffs": {}}]
GOLD_DIFF = [{"golden": "ldo-led", "status": "DIFF", "diffs": {"symbols": "-Device:R aa\n+Device:R bb\n"}}]


def runner(kb=KB_OK, gold=GOLD_OK, errors=()):
    seen = []

    def run(env):
        seen.append(env)
        return tu.Suites(list(kb), list(gold), list(errors))
    run.seen = seen
    return run


def git_repo(p):
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m", "init"]):
        subprocess.run(["git", "-C", str(p.root), *args], check=True)
    cfg = p.root / "ohmai.yaml"
    cfg.write_text(cfg.read_text() + "\nauthor: {name: Test, kind: human}\n")
    subprocess.run(["git", "-C", str(p.root), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "author",
                    "--no-verify"], check=True)
    return find_project(p.root)


def dep(p):
    return next(n for n in tc.tool_names() if n.startswith(tc.PY_PREFIX))


def test_clean_suites_move_the_pin_and_commit_only_the_lock(proj, monkeypatch):  # noqa: F811
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@t")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@t")
    p = git_repo(locked(proj))
    (p.root / "unrelated.txt").write_text("staged but not part of the upgrade\n")
    subprocess.run(["git", "-C", str(p.root), "add", "unrelated.txt"], check=True)
    name = dep(p)
    opts = fake(p, **{name: {"version": "2.0", "sha256": "d" * 64, "hash_scope": "machine"}})
    out = tu.upgrade(p, name, "2.0", opts=opts, runner=runner())
    assert out.ok, out.lines
    lock = tc.load_lock(p)
    assert lock["tools"][name]["version"] == "2.0"
    assert lock["history"][-1]["from"] == "1.0" and lock["history"][-1]["to"] == "2.0"
    show = subprocess.run(["git", "-C", str(p.root), "show", "--stat", "--format=%B", "HEAD"], capture_output=True,
                          text=True).stdout
    assert f"upgrade {name} 1.0 -> 2.0" in show and "OhmAI-Author: name=Test; kind=human" in show
    assert "toolchain.lock.yaml" in show and "unrelated.txt" not in show


def test_golden_diff_needs_an_explanation(proj):  # noqa: F811
    p = locked(proj)
    opts = fake(p, **{"kicad-app": {"version": "9.0.3", "sha256": "e" * 64, "hash_scope": "machine"}})
    run = runner(gold=GOLD_DIFF)
    out = tu.upgrade(p, "kicad-app", "9.0.3", opts=opts, runner=run, commit=False)
    assert not out.ok and any("NEEDS --explain" in ln for ln in out.lines)
    assert any("+Device:R bb" in ln for ln in out.lines), "the diff must be shown"
    assert tc.load_lock(p)["tools"]["kicad-app"]["version"] == "9.0.2"
    out = tu.upgrade(p, "kicad-app", "9.0.3", opts=opts, runner=run, commit=False,
                     explain="R symbol graphics changed upstream; pins identical")
    assert out.ok, out.lines
    h = tc.load_lock(p)["history"][-1]
    assert h["explain"].startswith("R symbol") and "golden ldo-led differs (symbols)" in h["explained"]


def test_known_bad_miss_blocks_even_with_explain(proj):  # noqa: F811
    p = locked(proj)
    opts = fake(p, **{"kicad-app": {"version": "9.0.3"}})
    kb = [{"case": "erc-vin-not-driven", "check": "erc", "caught": False, "skipped": False, "error": ""}]
    out = tu.upgrade(p, "kicad-app", "9.0.3", opts=opts, runner=runner(kb=kb), explain="looks fine", commit=False)
    assert not out.ok and any("BLOCKED: known-bad" in ln for ln in out.lines)
    assert tc.load_lock(p)["tools"]["kicad-app"]["version"] == "9.0.2"


@pytest.mark.parametrize("why", ["skipped", "no-coverage"])
def test_untested_upgrade_needs_an_explanation(proj, why):  # noqa: F811
    p = locked(proj)
    if why == "skipped":
        name, ver = "kicad-app", "9.0.3"
        run = runner(kb=[{"case": "x", "check": "erc", "caught": False, "skipped": "needs kicad-cli"}],
                     gold=[{"golden": "ldo-led", "status": "SKIP", "skipped": "needs kicad-cli", "diffs": {}}])
    else:
        name, ver = "freerouting", "2.1.0"
        run = runner()
    opts = fake(p, **{name: {"version": ver}})
    out = tu.upgrade(p, name, ver, opts=opts, runner=run, commit=False)
    assert not out.ok and any("NEEDS --explain" in ln for ln in out.lines), out.lines


def test_refusals(proj):  # noqa: F811
    p = locked(proj)
    assert not tu.upgrade(p, "git", "2.50", runner=runner()).ok             # tier 2
    assert not tu.upgrade(p, "nope", "1", runner=runner()).ok
    out = tu.upgrade(p, "kicad-app", "9.0.3", opts=fake(p), runner=runner(), commit=False)
    assert not out.ok and "not found" in out.lines[0]                    # candidate not installed
    out = tu.upgrade(p, "kicad-app", "10.0.0", opts=fake(p, **{"kicad-app": {"version": "10.0.0"}}),
                     runner=runner(), commit=False)
    assert not out.ok and "kicad.app" in out.lines[0]                    # policy is the owner's
    assert tu.upgrade(p, "kicad-app", "9.0.2", opts=fake(p), runner=runner()).ok   # already there


def test_kicad_libs_upgrade_moves_policy_and_tables(proj, tmp_path):  # noqa: F811
    p = locked(proj)
    libs = tmp_path / "libs"
    for sub in ("kicad-symbols-9.0.10", "kicad-footprints-9.0.10"):
        (libs / sub).mkdir()
    (libs / "kicad-symbols-9.0.10" / "Device.kicad_sym").write_text("(kicad_symbol_lib)\n")
    (libs / "kicad-footprints-9.0.10" / "Resistor_SMD.pretty").mkdir()
    run = runner()
    out = tu.upgrade(p, "kicad-libs", "9.0.10", runner=run, commit=False)
    assert out.ok, out.lines
    assert run.seen[-1] == {"OHMAI_KICAD_LIBS_TAG": "9.0.10"}
    p2 = find_project(p.root)
    assert (p2.config.get("kicad") or {}).get("libraries") == "9.0.10"
    assert "9.0.10" in (p2.root / "hardware" / "sym-lib-table").read_text()
    lock = yaml.safe_load((p2.root / tc.LOCK_NAME).read_text())
    assert lock["tools"]["kicad-libs"]["version"] == "9.0.10" and not tc.consistency(p2)
