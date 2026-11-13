# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`toolchain` check: installed tools vs the project's toolchain.lock.yaml (see ohmai/toolchain.py)."""

from __future__ import annotations

from ohmai import toolchain as tc
from ohmai.checks import FAIL, PASS, WARN, Result, register
from ohmai.project import Project


@register("toolchain", "Installed tools vs toolchain.lock.yaml: tier 1 (KiCad, libraries, Freerouting, KiKit, KiBot, "
                       "iBOM, Python deps) exact, FAIL on mismatch; tier 2 helpers WARN outside their range")
def check_toolchain(project: Project, opts: dict) -> Result:
    res = tc.verify(project, opts)
    if res is None:
        return Result("toolchain", WARN, f"no {tc.LOCK_NAME}: tool versions are not reproducible (run `ohmai lock` "
                      "and commit it)")
    probs = tc.consistency(project)
    fails = [m for s, m in res if s == FAIL] + probs
    warns = [m for s, m in res if s == WARN]
    oks = [m for s, m in res if s == PASS]
    t1 = sum(1 for n, e in ((tc.load_lock(project) or {}).get("tools") or {}).items()
             if isinstance(e, dict) and e.get("tier") == 1 and e.get("version"))
    summary = f"{len(res)} locked tool(s), {t1} tier-1 pinned; {len(fails)} mismatch(es), {len(warns)} warning(s)"
    if fails:
        return Result("toolchain", FAIL, summary, fails + warns)
    return Result("toolchain", WARN if warns else PASS, summary, warns or oks)


def lock_line(project: Project, name: str, found: dict | None) -> tuple[str, str] | None:
    """(status, message) for one tool against the lock, or None without a lock entry (used by kicad-app/-libs)."""
    lock = tc.load_lock(project)
    want = ((lock or {}).get("tools") or {}).get(name)
    if not isinstance(want, dict):
        return None
    return tc.verify_entry(name, want, found)
