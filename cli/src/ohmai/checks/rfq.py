# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""RFQ documents must stand alone: no internal tags, file paths or agent notes."""

from __future__ import annotations

import re

from ohmai.checks import FAIL, PASS, SKIP, Result, register
from ohmai.project import Project

FORBIDDEN = [
    (re.compile(r"\bTODO\b"), "TODO"),
    (re.compile(r"STATUS\.md|decisions\.md|AGENTS\.md|expected_nets"), "internal file name"),
    (re.compile(r"\bD-\d{3}\b"), "internal decision id"),
    (re.compile(r"\b(phase|fix)/[\w-]+"), "branch name"),
    (re.compile(r"\b(?:Q|OQ)-\d+\b"), "internal question id"),
    (re.compile(r"<!--"), "HTML comment / template marker"),
    (re.compile(r"\bagent\b|\bLLM\b|\bprompt\b", re.I), "agent wording"),
]


@register("rfq", "rfq/*.md are standalone vendor documents (no internal tags, paths, TODOs)")
def rfq(project: Project, opts: dict) -> Result:
    files = sorted((project.root / "rfq").glob("*.md"))
    files = [f for f in files if f.name.lower() != "readme.md"]
    if not files:
        return Result("rfq", SKIP, "no rfq/*.md (fine if no supplier questions are needed)")
    errors = []
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), start=1):
            for rx, why in FORBIDDEN:
                if rx.search(line):
                    errors.append(f"{f.name}:{i}: {why}: {line.strip()[:100]}")
    if errors:
        return Result("rfq", FAIL, f"{len(errors)} internal reference(s) in {len(files)} RFQ(s)", errors)
    return Result("rfq", PASS, f"{len(files)} RFQ(s) stand alone")
