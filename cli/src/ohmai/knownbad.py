# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Known-bad suite (mutation fixtures): every check must FAIL on a planted mistake and PASS on its clean twin.

Layout: <tests>/fixtures/known-bad/<case>/case.yaml (+ optional files/), shared bases in _base/<name>/.

    check: passives                      # the check under test (as `ohmai check <name>`)
    bug: "VIN input capacitor C1 missing"
    detail: "VIN"                        # regex the bad result (summary + details) must match: caught for
                                         # the right reason, not by accident
    base: ["golden:ldo-led", ldo-led]    # overlays in order: golden:<name> = tests/golden/<name>/src,
                                         # else _base/<name>/ (files/ + config.yaml)
    name: kb                             # project name (hardware/<name>.kicad_sch), default kb
    needs: [kicad-cli, git, kicad-libs]  # skipped (not failed) when missing
    config: {...}                        # deep-merged into ohmai.yaml (both twins)
    opts: {netlist: board.net}           # check options; file values are relative to the project root
    bad_opts: {...}                      # merged into opts for the bad twin (the bug is in the environment,
                                         # e.g. the wrong KiCad installed). "@pinned-libs" in opts = the
                                         # libraries tag in effect (so a canary/upgrade candidate tag works)
    fake_kicad_libs: true                # empty pinned library dirs in a temp OHMAI_KICAD_LIBS + lib tables
    lock: true                           # `ohmai lock` (with opts' toolchain overrides) before the final stage
    prepare: [edit, ...]                 # both twins
    mutate:  [edit, ...]                 # bad twin only: the planted mistake
    build: true                          # `ohmai kicad-libs tables` + `ohmai schematic build` (after files)
    git: {commits: [{message: ..., files: {path: text}}]}   # repo with a trailer-clean main + phase branch
    review: {phase: constraints, reviewer: reviewer-a, vendor: VendorB, model: m9, independence: 1, rows: [...]}
                                         # `ohmai review start` + a review file stamped with the current hash
    expect: {clean: PASS, bad: FAIL}     # defaults

Edits: {file, replace, with} (the old text must exist) | {file, write} | {file, append} | {file, delete: true}
| {copy: src, to: dst} | {commit: i, replace, with} (a git commit message). `stage:` files (default: before
build/git/review) | built (after build) | final (after git and review). `*.net.yaml` compile to `*.net`
(ohmai.testkit) after the files stage.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ohmai import kicad, kicadlibs
from ohmai.checks import Result
from ohmai.project import find_project
from ohmai.resources import tests_dir

TRAILER = "OhmAI-Author: name=OhmAI; kind=agent; vendor=Fixture; model=none; instructions=known-bad"
GIT_ID = ["-c", "user.name=known-bad", "-c", "user.email=kb@localhost", "-c", "commit.gpgsign=false"]


class CaseError(Exception):
    """The fixture itself is broken (an edit target is missing, a file does not exist)."""


@dataclass
class Case:
    name: str
    dir: Path
    spec: dict

    @property
    def check(self) -> str:
        return str(self.spec["check"])

    @property
    def bug(self) -> str:
        return str(self.spec.get("bug", ""))


@dataclass
class Outcome:
    case: Case
    clean: Result | None = None
    bad: Result | None = None
    skipped: str = ""
    error: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def caught(self) -> bool:
        return not self.skipped and not self.error and not self.problems

    def as_dict(self) -> dict:
        return {"case": self.case.name, "check": self.case.check, "bug": self.case.bug,
                "clean": self.clean.status if self.clean else None, "bad": self.bad.status if self.bad else None,
                "caught": self.caught, "skipped": self.skipped, "error": self.error, "problems": self.problems}


def fixtures_dir() -> Path:
    return tests_dir() / "fixtures" / "known-bad"


def load_cases(root: Path | None = None, names: list[str] | None = None) -> list[Case]:
    root = root or fixtures_dir()
    out = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        if names and d.name not in names:
            continue
        f = d / "case.yaml"
        if f.is_file():
            out.append(Case(d.name, d, yaml.safe_load(f.read_text(encoding="utf-8")) or {}))
    if names:
        missing = sorted(set(names) - {c.name for c in out})
        if missing:
            raise CaseError(f"unknown known-bad case(s): {', '.join(missing)}")
    return out


def missing_needs(needs: list[str]) -> list[str]:
    miss = []
    for n in needs or []:
        if n == "kicad-cli" and not kicad.kicad_cli():
            miss.append("kicad-cli")
        elif n == "git" and not shutil.which("git"):
            miss.append("git")
        elif n == "skidl":
            from ohmai.checks.skidlnet import skidl_python, skidl_version

            if not skidl_version(skidl_python()):
                miss.append("skidl (optional: pip install skidl)")
        elif n == "kicad-libs":
            tag = os.environ.get(kicadlibs.TAG_ENV) or _template_libs_tag()
            if not all(d.is_dir() for d in kicadlibs.dirs(tag)):
                miss.append(f"kicad-libs {tag} (ohmai kicad-libs install)")
    return miss


def _template_libs_tag() -> str:
    from ohmai.resources import template_dir

    cfg = yaml.safe_load((template_dir() / "ohmai.yaml").read_text(encoding="utf-8").replace("{{", "").replace("}}", ""))
    return str((cfg.get("kicad") or {}).get("libraries"))


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _edit(root: Path, e: dict, commits: list[dict] | None = None) -> None:
    if "commit" in e:
        msg = commits[int(e["commit"])]["message"]
        if e["replace"] not in msg:
            raise CaseError(f"commit {e['commit']} message has no {e['replace']!r}")
        commits[int(e["commit"])]["message"] = msg.replace(e["replace"], e.get("with", ""))
        return
    if "copy" in e:
        src, dst = root / e["copy"], root / e["to"]
        if not src.is_file():
            raise CaseError(f"copy: {e['copy']} not found")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    f = root / e["file"]
    if e.get("delete"):
        if not f.exists():
            raise CaseError(f"delete: {e['file']} not found")
        f.unlink()
    elif "write" in e:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(e["write"], encoding="utf-8")
    elif "append" in e:
        if not f.is_file():
            raise CaseError(f"append: {e['file']} not found")
        f.write_text(f.read_text(encoding="utf-8") + e["append"], encoding="utf-8")
    elif "replace" in e:
        if not f.is_file():
            raise CaseError(f"replace: {e['file']} not found")
        text = f.read_text(encoding="utf-8")
        old = e["replace"]
        if e.get("regex"):
            new, n = re.subn(old, e.get("with", ""), text, count=int(e.get("count", 0)), flags=re.M)
        else:
            n = text.count(old)
            new = text.replace(old, e.get("with", ""), int(e["count"]) if e.get("count") else -1)
        if not n:
            raise CaseError(f"replace: {old!r} not in {e['file']}")
        f.write_text(new, encoding="utf-8")
    else:
        raise CaseError(f"unknown edit {e}")


def _git(root: Path, *a: str) -> None:
    r = subprocess.run(["git", "-C", str(root), *GIT_ID, *a], capture_output=True, text=True)
    if r.returncode != 0:
        raise CaseError(f"git {' '.join(a)}: {r.stderr.strip()}")


def _review(project, spec: dict) -> None:
    from ohmai.review import content_hash, gates, review_dir, scope_files, start

    phase = spec["phase"]
    if spec.get("brief", True):
        start(project, phase, today="2026-10-15")
    h = content_hash(project, scope_files(project, gates(project)[phase]))[0]
    rows = spec.get("rows") or [f"| F1 | {spec.get('file', '-')} | OK | | |"]
    body = "\n".join(["---", f"reviewer: {spec.get('reviewer', 'reviewer-a')}", "kind: agent",
                      f"vendor: {spec.get('vendor', 'VendorB')}", f"model: {spec.get('model', 'm9')}",
                      f"instructions: {spec.get('instructions', 'reviewer')}",
                      f"independence: {spec.get('independence', 1)}", f"files_hash: '{h}'", "date: 2026-10-15", "---",
                      "| ID | File | Verdict | Finding | Cite |", "|---|---|---|---|---|", *rows, ""])
    d = review_dir(project, phase)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec.get('reviewer', 'reviewer-a')}.md").write_text(body, encoding="utf-8")


def build_twin(case: Case, dest: Path, bad: bool) -> tuple:
    """Create one twin project in dest; returns (project, opts)."""
    from ohmai.newproject import create

    sp = case.spec
    create(dest, "known-bad fixture: " + case.bug, sp.get("name", "kb"), git=False)
    base_root = case.dir.parent / "_base"
    config: dict = {}
    for b in sp.get("base") or []:
        if str(b).startswith("golden:"):
            src = tests_dir() / "golden" / str(b)[7:] / "src"
            if not src.is_dir():
                raise CaseError(f"base {b}: {src} not found")
            shutil.copytree(src, dest, dirs_exist_ok=True)
            continue
        bd = base_root / b
        if not bd.is_dir():
            raise CaseError(f"base {b} not found in {base_root}")
        if (bd / "files").is_dir():
            shutil.copytree(bd / "files", dest, dirs_exist_ok=True)
        if (bd / "config.yaml").is_file():
            config = _merge(config, yaml.safe_load((bd / "config.yaml").read_text(encoding="utf-8")) or {})
    if (case.dir / "files").is_dir():
        shutil.copytree(case.dir / "files", dest, dirs_exist_ok=True)
    config = _merge(config, sp.get("config") or {})
    if config:
        cfg_f = dest / "ohmai.yaml"
        cfg = yaml.safe_load(cfg_f.read_text(encoding="utf-8")) or {}
        cfg_f.write_text(yaml.safe_dump(_merge(cfg, config), sort_keys=False), encoding="utf-8")
    if sp.get("fake_kicad_libs"):
        libs = dest / ".fake-kicad-libs"
        tag = kicadlibs.pinned(find_project(dest))
        sym, fp = libs / f"kicad-symbols-{tag}", libs / f"kicad-footprints-{tag}"
        (fp / "Resistor_SMD.pretty").mkdir(parents=True)
        sym.mkdir(parents=True)
        (sym / "Device.kicad_sym").write_text("(kicad_symbol_lib (version 20241209))\n", encoding="utf-8")
        os.environ["OHMAI_KICAD_LIBS"] = str(libs)
        kicadlibs.write_tables(find_project(dest))
    commits = [dict(c) for c in ((sp.get("git") or {}).get("commits") or [])]
    edits = list(sp.get("prepare") or []) + (list(sp.get("mutate") or []) if bad else [])

    def stage(name: str) -> None:
        for e in edits:
            if e.get("stage", "files") == name:
                _edit(dest, e, commits)

    stage("files")
    for f in dest.rglob("*.net.yaml"):
        from ohmai.testkit import compile_file

        compile_file(f)
    if sp.get("build"):
        from ohmai.schgen import build

        p = find_project(dest)
        kicadlibs.activate(p)
        kicadlibs.write_tables(p)
        build(p)
    stage("built")
    if sp.get("git") is not None:
        _git(dest, "init", "-q", "-b", "main")
        _git(dest, "add", "-A")
        _git(dest, "commit", "-q", "-m", "fixture base", "-m", TRAILER)
        _git(dest, "checkout", "-q", "-b", "phase/fixture")
        from ohmai import trailers as tr

        tr.install_hook(dest)
        for c in commits:
            for rel, text in (c.get("files") or {}).items():
                (dest / rel).parent.mkdir(parents=True, exist_ok=True)
                (dest / rel).write_text(text, encoding="utf-8")
            _git(dest, "add", "-A")
            _git(dest, "commit", "-q", "--allow-empty", "--no-verify", "-m", c["message"])
    if sp.get("review"):
        _review(find_project(dest), sp["review"])
    marks = {PINNED_LIBS: kicadlibs.pinned(find_project(dest)) or ""}
    opts = _subst(_merge(dict(sp.get("opts") or {}), (sp.get("bad_opts") or {}) if bad else {}), marks)
    if sp.get("lock"):
        from ohmai import toolchain as tc

        p = find_project(dest)
        tc.write_lock(p, tc.make_lock(p, _subst(dict(sp.get("opts") or {}), marks))[0])
    stage("final")
    for k in ("netlist", "fab"):
        if opts.get(k):
            opts[k] = str(dest / opts[k])
    return find_project(dest), opts


PINNED_LIBS = "@pinned-libs"   # opts placeholder: the libraries tag in effect (ohmai.yaml or OHMAI_KICAD_LIBS_TAG)


def _subst(obj, marks: dict[str, str]):
    if isinstance(obj, dict):
        return {k: _subst(v, marks) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_subst(v, marks) for v in obj]
    return marks.get(obj, obj) if isinstance(obj, str) else obj


def run_case(case: Case, work: Path) -> Outcome:
    from ohmai.checks import FAIL, PASS, run

    out = Outcome(case)
    miss = missing_needs(case.spec.get("needs") or [])
    if miss:
        out.skipped = "needs " + ", ".join(miss)
        return out
    expect = {"clean": PASS, "bad": FAIL, **(case.spec.get("expect") or {})}
    env_ci = os.environ.pop("CI", None)   # the commits check skips its hook warning under CI
    env_libs = os.environ.get("OHMAI_KICAD_LIBS")
    cwd = Path.cwd()
    try:
        for twin in ("clean", "bad"):
            d = work / case.name / twin
            if d.exists():
                shutil.rmtree(d)
            d.parent.mkdir(parents=True, exist_ok=True)
            project, opts = build_twin(case, d, bad=twin == "bad")
            os.chdir(project.root)
            res = run(project, case.check, opts)
            setattr(out, twin, res)
            want = expect[twin]
            want = want if isinstance(want, list) else [want]
            if res.status not in want:
                out.problems.append(f"{twin} twin: {res.status} (expected {'/'.join(want)}): {res.summary}"
                                    + (f" | {res.details[0]}" if res.details else ""))
        rx = case.spec.get("detail")
        if rx and out.bad is not None and out.bad.status == FAIL:
            text = "\n".join([out.bad.summary, *out.bad.details])
            if not re.search(rx, text):
                out.problems.append(f"bad twin FAILs, but not for the planted bug (no /{rx}/ in its report)")
    except (CaseError, SystemExit, OSError, subprocess.SubprocessError) as e:
        out.error = f"{type(e).__name__}: {e}"
    finally:
        os.chdir(cwd)
        if env_ci is not None:
            os.environ["CI"] = env_ci
        if env_libs is None:
            os.environ.pop("OHMAI_KICAD_LIBS", None)
        else:
            os.environ["OHMAI_KICAD_LIBS"] = env_libs
    return out


def run_all(work: Path, names: list[str] | None = None) -> list[Outcome]:
    return [run_case(c, work) for c in load_cases(names=names)]


# Checks without a planted-bug case, with the reason (the coverage test requires every registered check to
# have a case or an entry here).
NOT_COVERED = {
    "creepage": "TODO check (not implemented): it reports TODO for every board, so nothing can be caught yet",
}


def table(outcomes: list[Outcome]) -> list[str]:
    rows = ["| Case | Check | Planted bug | Clean | Bad | Caught |", "|---|---|---|---|---|---|"]
    for o in outcomes:
        exp_bad = (o.case.spec.get("expect") or {}).get("bad", "FAIL")
        verdict = (f"yes ({exp_bad} by design)" if exp_bad != "FAIL" else "yes") if o.caught else ("skipped: " + o.skipped if o.skipped else
                                          "ERROR: " + o.error if o.error else "NO: " + "; ".join(o.problems))
        rows.append(f"| {o.case.name} | `{o.case.check}` | {o.case.bug} | {o.clean.status if o.clean else '-'} | "
                    f"{o.bad.status if o.bad else '-'} | {verdict.replace('|', '/')} |")
    return rows
