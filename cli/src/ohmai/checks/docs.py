# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Document checks: the file exists and the template marker has been removed; STATUS.md open questions."""

from __future__ import annotations

from ohmai.checks import FAIL, PASS, SKIP, WARN, Result, register
from ohmai.project import Project

MARKER = "<!-- ohmai:template"
NA_MARKER = "<!-- ohmai:not-applicable"


def check_doc(project: Project, rel: str) -> Result:
    name = f"docs:{rel}"
    p = project.root / rel
    if not p.is_file():
        return Result(name, FAIL, f"{rel} missing")
    text = p.read_text(encoding="utf-8")
    if NA_MARKER in text and rel == "docs/tool-issues.md":
        return Result(name, FAIL, "tool issue report is mandatory and cannot be marked not applicable")
    if NA_MARKER in text:
        return Result(name, SKIP, f"{rel} marked not applicable")
    if MARKER in text:
        return Result(name, FAIL, f"{rel} still has the template marker; fill it in, then delete the marker line")
    todos = [ln.strip() for ln in text.splitlines() if "TODO" in ln]
    if todos:
        return Result(name, FAIL, f"{rel} has {len(todos)} TODO line(s)", todos[:20])
    return Result(name, PASS, f"{rel} filled in")


NON_BLOCKING = "non-blocking"


@register(
    "questions",
    "STATUS.md open questions: WARN while blocking ones remain (a docs PASS is not an owner lock)",
)
def check_questions(project: Project, opts: dict) -> Result:
    from ohmai.status import load

    st = load(project.root / "STATUS.md")
    if st.problems:
        return Result("questions", FAIL, "STATUS.md has problems", st.problems)
    blocking = [q for q in st.open_questions if NON_BLOCKING not in q.lower()]
    if not st.markets:
        blocking.append("target markets not set: STATUS.md front matter 'markets:' (e.g. [EU, UK, US]); "
                        "they drive the phase 3 regulations, marks and the manual")
    if blocking:
        return Result(
            "questions",
            WARN,
            f"{len(blocking)} blocking open question(s) in STATUS.md; the owner answers them at the gate "
            "(mark deferred ones 'Non-blocking')",
            blocking[:20],
        )
    deferred = len(st.open_questions)
    return Result("questions", PASS, f"no blocking open questions ({deferred} non-blocking)")
