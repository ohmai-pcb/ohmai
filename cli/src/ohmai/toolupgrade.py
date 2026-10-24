"""`ohmai upgrade <tool> <version>`: move one tier-1 pin, only after the golden + known-bad suites pass.

1. The candidate must be installed and selectable: `--path` (KICAD_CLI, FREEROUTING_JAR, OHMAI_IBOM_DIR,
   OHMAI_JLCPARTS_DB) or, for kicad-libs, the tag (OHMAI_KICAD_LIBS_TAG; `--install` downloads it). Python
   packages: install the candidate into this environment first (pip install <pkg>==<version>).
2. The known-bad suite (every check still catches its planted bug) and the golden suite (reference
   projects rebuild to the approved snapshots) run in a subprocess with the candidate selected.
3. The lock moves only when both are clean, or when the golden diffs, or the absence of any suite that
   exercises the tool, are explained with `--explain` (logged in the lock history and the commit). A
   known-bad miss is never explainable: a check that stops catching its bug blocks the upgrade.
4. The lock (and for kicad-libs ohmai.yaml kicad.libraries + the lib tables) is committed on its own.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ohmai import kicadlibs
from ohmai import toolchain as tc
from ohmai import trailers as tr
from ohmai.project import Project, find_project
from ohmai.versions import version_in_range

PATH_ENV = {"kicad-app": "KICAD_CLI", "freerouting": "FREEROUTING_JAR", "ibom": "OHMAI_IBOM_DIR",
            "jlcparts": "OHMAI_JLCPARTS_DB"}
# Which suites exercise a tool. Tools not listed: no suite covers them yet, so --explain is required.
COVERED = {"kicad-app": ("known-bad", "golden"), "kicad-libs": ("known-bad", "golden")}


def coverage(name: str) -> tuple[str, ...]:
    if name in COVERED:
        return COVERED[name]
    if name.startswith(tc.PY_PREFIX):
        return ("known-bad", "golden")   # ohmai itself runs on them in both suites
    return ()


@dataclass
class Suites:
    known_bad: list[dict] = field(default_factory=list)
    golden: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def misses(self) -> list[str]:
        return [f"{o['case']} ({o['check']}): {o.get('error') or 'bug not caught'}"
                for o in self.known_bad if not o.get("caught") and not o.get("skipped")]

    def diffs(self) -> dict[str, dict[str, str]]:
        return {g["golden"]: g["diffs"] for g in self.golden if g.get("diffs")}

    def golden_errors(self) -> list[str]:
        return [f"{g['golden']}: {g['error']}" for g in self.golden if g.get("status") == "ERROR"]

    def ran(self, suite: str) -> int:
        rows = self.known_bad if suite == "known-bad" else self.golden
        return sum(1 for r in rows if not r.get("skipped"))

    def summary(self) -> str:
        kb = self.known_bad
        caught = sum(1 for o in kb if o.get("caught"))
        skipped = sum(1 for o in kb if o.get("skipped"))
        gold = ", ".join(f"{g['golden']} {g['status']}" for g in self.golden) or "none"
        return f"known-bad {caught}/{len(kb)} caught ({skipped} skipped); golden: {gold}"


def run_suites(env: dict[str, str]) -> Suites:
    """Both suites in a subprocess with the candidate environment."""
    s = Suites()
    full = {**os.environ, **env}
    for suite, attr in (("known-bad", "known_bad"), ("golden", "golden")):
        r = subprocess.run([sys.executable, "-m", "ohmai", suite, "--json"], capture_output=True, text=True, env=full,
                           timeout=3600)
        try:
            setattr(s, attr, json.loads(r.stdout))
        except json.JSONDecodeError:
            s.errors.append(f"{suite}: exit {r.returncode}: {(r.stderr or r.stdout).strip()[-800:]}")
    return s


@contextmanager
def environ(env: dict[str, str]):
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def candidate_env(name: str, version: str, path: str | None) -> dict[str, str]:
    env = {}
    if name == "kicad-libs":
        env[kicadlibs.TAG_ENV] = version
    if path:
        if name not in PATH_ENV:
            raise SystemExit(f"ohmai upgrade: --path is not used for {name} (install the candidate instead)")
        env[PATH_ENV[name]] = str(Path(path).expanduser().resolve())
    return env


@dataclass
class Outcome:
    ok: bool
    lines: list[str]
    changed: list[Path] = field(default_factory=list)
    commit: str = ""


def _set_libraries(cfg: Path, tag: str) -> None:
    t = cfg.read_text(encoding="utf-8")
    new, n = re.subn(r'^(\s+libraries:\s*)("[^"]*"|\'[^\']*\'|[^\s#]+)', rf'\g<1>"{tag}"', t, count=1, flags=re.M)
    if n != 1:
        raise SystemExit("ohmai upgrade: no `kicad: libraries:` line in ohmai.yaml to update")
    cfg.write_text(new, encoding="utf-8")


def upgrade(project: Project, name: str, version: str, *, explain: str = "", path: str | None = None,
            install: bool = False, commit: bool = True, dry_run: bool = False, opts: dict | None = None,
            runner: Callable[[dict[str, str]], Suites] = run_suites) -> Outcome:
    L: list[str] = []
    t = tc.tool(name)
    if t is None:
        return Outcome(False, [f"unknown tool {name!r}; known: {', '.join(tc.tool_names())}"])
    if t.tier != 1:
        return Outcome(False, [f"{name} is a tier-2 helper: edit its `range` in {tc.LOCK_NAME} (owner decision); "
                               "`ohmai upgrade` moves tier-1 pins"])
    lock = tc.load_lock(project)
    if lock is None:
        return Outcome(False, [f"no {tc.LOCK_NAME}: run `ohmai lock` first"])
    old = (lock.get("tools") or {}).get(name)
    old_v = (old or {}).get("version")
    if old is not None and str(old_v) == version:
        return Outcome(True, [f"{name} is already locked at {version}"])
    if name == "kicad-app":
        spec = (project.config.get("kicad") or {}).get("app")
        if spec:
            ok, err = version_in_range(version, str(spec))
            if err or not ok:
                return Outcome(False, [f"kicad-app {version} is outside ohmai.yaml kicad.app {spec!r}: widening the "
                                       "policy is an owner decision (edit ohmai.yaml first)"])
    env = candidate_env(name, version, path)
    if name == "kicad-libs" and install:
        L.append(f"installing KiCad libraries {version} into {kicadlibs.base_dir()}")
        kicadlibs.install(version)
    with environ(env):
        found = tc.detect(project, name, opts)
    if found is None or str(found.get("version")) != version:
        hint = f" ({t.install_hint})" if t.install_hint else ""
        return Outcome(False, [f"candidate {name} {version} not found: detected "
                               f"{found.get('version') if found else 'nothing'}{hint}"])
    L.append(f"candidate {name} {version} (lock: {old_v}); env {env or '(current)'}")
    s = runner(env)
    L.append("suites: " + s.summary())
    blockers, need_explain = list(s.errors), []
    misses = s.misses()
    if misses:
        blockers.append("known-bad: a check stopped catching its planted bug (not explainable; fix the check or "
                        "stay on the current pin):\n    " + "\n    ".join(misses))
    blockers += [f"golden {e}" for e in s.golden_errors()]
    for g, diffs in s.diffs().items():
        for art, d in sorted(diffs.items()):
            L.append(f"--- golden {g}: {art} differs ---\n{d.rstrip()}")
        need_explain.append(f"golden {g} differs ({', '.join(sorted(diffs))})")
    cov = coverage(name)
    if not cov:
        need_explain.append(f"no suite exercises {name} yet")
    elif not any(s.ran(x) for x in cov):
        need_explain.append(f"the suites that exercise {name} were all skipped (missing tools)")
    if blockers:
        return Outcome(False, L + ["BLOCKED: " + b for b in blockers] + ["lock unchanged"])
    if need_explain and not explain.strip():
        return Outcome(False, L + [f"NEEDS --explain: {n}" for n in need_explain] + ["lock unchanged"])
    if dry_run:
        return Outcome(True, L + ["dry run: lock unchanged"])
    with environ(env):
        new = tc.entry_for(project, name, found, old)
    lock.setdefault("tools", {})[name] = new
    hist = {"date": date.today().isoformat(), "tool": name, "from": old_v, "to": version, "suites": s.summary()}
    if need_explain:
        hist["explained"] = need_explain
        hist["explain"] = explain.strip()
    lock["history"] = [*(lock.get("history") or []), hist]
    lock["generated"] = {"date": date.today().isoformat(), "by": f"ohmai upgrade {name} {version}"}
    changed = [tc.write_lock(project, lock)]
    if name == "kicad-libs":
        _set_libraries(project.root / "ohmai.yaml", version)
        project = find_project(project.root)
        if all(p.is_file() for p in kicadlibs.table_paths(project)):
            changed += list(kicadlibs.write_tables(project))
        changed.append(project.root / "ohmai.yaml")
    L.append(f"lock: {name} {old_v} -> {version}")
    out = Outcome(True, L, changed)
    if commit:
        out.commit = _commit(project, name, old_v, version, s, need_explain, explain, changed)
        out.lines.append(f"committed {out.commit}")
    return out


def _commit(project: Project, name, old_v, version, s: Suites, need, explain, files: list[Path]) -> str:
    root = project.root
    if not tr.is_repo(root):
        raise SystemExit("ohmai upgrade: not a git repo (use --no-commit)")
    value = tr.format_trailer(tr.configured_identity(project.config))
    probs = tr.validate(value) if value else ["no identity configured"]
    if probs:
        raise SystemExit(f"ohmai upgrade: cannot build the {tr.KEY} trailer ({'; '.join(probs)}); set ohmai.yaml "
                         "`author:` or OHMAI_AUTHOR_*, or use --no-commit")
    body = [f"toolchain: upgrade {name} {old_v} -> {version}", "", f"Suites: {s.summary()}."]
    if need:
        body += ["", "Explained: " + "; ".join(need) + ".", explain.strip()]
    msg = "\n".join(body) + f"\n\n{tr.KEY}: {value}\n"
    rel = [str(f.relative_to(root)) for f in files]
    subprocess.run(["git", "-C", str(root), "add", "--", *rel], check=True)
    r = subprocess.run(["git", "-C", str(root), "commit", "-q", "-F", "-", "--", *rel], input=msg, text=True,
                       capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"ohmai upgrade: git commit failed: {r.stderr.strip() or r.stdout.strip()}")
    return subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
