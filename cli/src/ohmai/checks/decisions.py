# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""`decisions`: every decision id cited in STATUS.md or a review resolutions file has a record in
docs/decisions.md.

Lesson from dogfooding: a waiver decision lived only in STATUS.md for several review rounds and
docs/decisions.md never got the row. The decision log is the durable record, STATUS is a
dashboard, so a cited id with no log entry is a FAIL.
"""

from __future__ import annotations

import re

from ohmai.checks import FAIL, PASS, SKIP, Result, register
from ohmai.project import Project

ID = re.compile(r"\bD-\d{3}\b")
# An entry is a table row or a heading that starts with the id; a mention inside another entry does not count.
ENTRY = re.compile(r"^\s*(?:\|\s*|#+\s*)(D-\d{3})\b", re.M)


@register("decisions", "Every D-xxx cited in STATUS.md or reviews/*/resolutions.md is recorded in docs/decisions.md")
def decisions(project: Project, opts: dict) -> Result:
    root = project.root
    log = root / "docs" / "decisions.md"
    sources = [root / "STATUS.md", *sorted((root / "reviews").glob("*/resolutions.md"))]
    sources = [p for p in sources if p.is_file()]
    cited: dict[str, str] = {}
    for p in sources:
        for i in ID.findall(p.read_text(encoding="utf-8", errors="replace")):
            cited.setdefault(i, str(p.relative_to(root)))
    if not cited:
        return Result("decisions", SKIP, "no decision ids cited in STATUS.md or review resolutions")
    if not log.is_file():
        return Result("decisions", FAIL, f"{len(cited)} decision id(s) cited but docs/decisions.md is missing",
                      sorted(cited))
    have = set(ENTRY.findall(log.read_text(encoding="utf-8", errors="replace")))
    missing = [f"{i} (cited in {cited[i]}) has no entry in docs/decisions.md" for i in sorted(cited) if i not in have]
    if missing:
        return Result("decisions", FAIL, f"{len(missing)} cited decision(s) not in the decision log", missing)
    return Result("decisions", PASS, f"{len(cited)} cited decision id(s) all recorded")
