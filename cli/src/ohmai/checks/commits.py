# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`commits` check: every commit on the phase branch carries a well-formed `OhmAI-Author:` trailer."""

from __future__ import annotations

import os

from ohmai.checks import FAIL, PASS, SKIP, WARN, Result, register
from ohmai.project import Project


@register("commits", "Every commit on the phase branch since main has a well-formed OhmAI-Author trailer "
                     "(pre-`commits.trailer_required_since` history WARNs)")
def check_commits(project: Project, opts: dict | None = None) -> Result:
    from ohmai import trailers as tr

    root = project.root
    if not tr.is_repo(root):
        return Result("commits", SKIP, "not a git repository")
    anon_errs = tr.check_pre_push(root) if project.config.get("anonymous") else []
    rng, desc = tr.phase_range(root, project.config)
    if not rng:
        if anon_errs:
            return Result("commits", FAIL, f"anonymous repo hygiene check failed ({len(anon_errs)} issue(s))", anon_errs)
        return Result("commits", PASS, f"no commits to check ({desc})")
    old = tr.grandfathered(root, project.config)
    ref = tr.cutoff_ref(project.config)
    if ref and not old:
        return Result("commits", FAIL, f"commits.trailer_required_since '{ref}' is not a commit in this repo")
    errs, warns, n, old_n = [], [], 0, 0
    for c in tr.commits_in(root, rng):
        n += 1
        probs = [] if c["trailers"] else [f"no `{tr.KEY}:` trailer"]
        for v in c["trailers"]:
            probs += tr.validate(v)
        if len(c["trailers"]) > 1:
            probs.append(f"{len(c['trailers'])} `{tr.KEY}:` trailers (one per commit)")
        if not probs:
            continue
        line = f"{c['sha'][:7]} {c['subject'][:60]}: {'; '.join(probs)}"
        if c["sha"] in old:
            old_n += 1
            warns.append(f"grandfathered: {line}")
        else:
            errs.append(line)
    ok, hook = tr.hook_status(root)
    if not ok and not os.environ.get("CI"):   # hooks are not versioned: a CI clone never has one
        warns.append(f"commit-msg hook: {hook}")
    summary = f"{n} commit(s) {desc}; {len(errs)} without a valid trailer"
    if old_n:
        summary += f", {old_n} grandfathered (reachable from trailer_required_since {ref})"
    errs += anon_errs
    if errs:
        hint = [f"add `{tr.KEY}: name=...; kind=agent|human; vendor=...; model=...; instructions=...` "
                f"(use `ohmai commit`); for history you may not rewrite, set commits.trailer_required_since "
                f"in ohmai.yaml (owner decision)"]
        return Result("commits", FAIL, summary, errs + warns + hint)
    return Result("commits", WARN if warns else PASS, summary, warns)
