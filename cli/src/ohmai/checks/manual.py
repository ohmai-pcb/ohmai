# SPDX-License-Identifier: Apache-2.0
"""User-manual checks.

`manual-warnings` (phases 2-3): every manual warning ID (MW-xx) named in the architecture or safety
plan is registered in STATUS.md "Manual warnings", and the safety plan lists them all.
`manual` (phase 12): docs/manual.md is filled in, has the required sections, covers the rules for each
target market in STATUS.md `markets:`, and references every manual warning.

Market rules are text checks for the presence of required information, not a legal review.
Sources are in template/docs/manual.md and skill/phases/03-safety.md.
"""

from __future__ import annotations

import re

from ohmai.checks import FAIL, PASS, WARN, Result, register
from ohmai.checks.docs import MARKER
from ohmai.project import Project

MW_RE = re.compile(r"\bMW-\d{2,}\b")

SECTIONS = (
    "product identification",
    "manufacturer and contact",
    "intended use",
    "ratings and specifications",
    "safety information",
    "installation and use",
    "maintenance and cleaning",
    "disposal",
    "conformity",
    "language versions",
)

# market -> [(what, regex, source)]; regexes are case-insensitive presence checks
MARKET_RULES: dict[str, list[tuple[str, str, str]]] = {
    "EU": [
        ("type, batch or serial number", r"\b(serial|batch)\b", "GPSR (EU) 2023/988 Art. 9(5); EMC 2014/30/EU Art. 7(5)"),
        ("manufacturer postal address", r"postal|street|\b\d{3} ?\d{2}\b", "GPSR Art. 9(6); EMC Art. 7(6)"),
        ("manufacturer electronic address", r"@|e-?mail|https?://", "GPSR Art. 9(6)"),
        ("economic operator established in the EU", r"economic operator|established in the EU|authori[sz]ed representative|importer",
         "Reg. (EU) 2019/1020 Art. 4; GPSR Art. 16"),
        ("CE marking", r"\bCE\b", "Reg. (EC) 765/2008 Art. 30; EMC Art. 16-17"),
        ("EU declaration of conformity reference", r"EU declaration of conformity", "EMC Art. 15; RoHS 2011/65/EU Art. 7(c)"),
        ("crossed-out wheeled bin (WEEE)", r"wheeled bin", "WEEE 2012/19/EU Art. 14(2)(e), 14(4), Annex IX"),
        ("language of the market", r"language", "GPSR Art. 9(7); EMC Art. 7(7)"),
    ],
    "UK": [
        ("UKCA or CE marking", r"\bUKCA\b|\bCE\b", "UK Electromagnetic Compatibility Regulations 2016; verify in phase 3"),
        ("declaration of conformity reference", r"declaration of conformity", "UK EMC Regulations 2016; verify in phase 3"),
        ("crossed-out wheeled bin (WEEE)", r"wheeled bin", "UK WEEE Regulations 2013; verify in phase 3"),
    ],
    "US": [
        ("FCC Part 15 compliance statement", r"FCC|Part 15", "47 CFR 15.19, 15.105; verify in phase 3"),
        ("changes or modifications warning", r"changes or modifications", "47 CFR 15.21; verify in phase 3"),
    ],
}


def _read(project: Project, rel: str) -> str:
    p = project.root / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def _doc_warnings(project: Project) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for rel in ("docs/architecture.md", "docs/safety.md"):
        for mw in MW_RE.findall(_read(project, rel)):
            out.setdefault(mw, set()).add(rel)
    return out


def _sections(text: str) -> set[str]:
    return {m.group(1).strip().lower() for m in re.finditer(r"^##\s+(.*?)\s*$", text, re.M)}


@register(
    "manual-warnings",
    "Manual warnings (MW-xx) named in architecture/safety docs are registered in STATUS.md and the safety plan",
)
def check_manual_warnings(project: Project, opts: dict) -> Result:
    from ohmai.status import load

    st = load(project.root / "STATUS.md")
    registered = set(st.manual_warnings)
    named = _doc_warnings(project)
    problems = [f"{mw} named in {', '.join(sorted(src))} but not under STATUS.md 'Manual warnings'"
                for mw, src in sorted(named.items()) if mw not in registered]
    safety = _read(project, "docs/safety.md")
    if safety and MARKER not in safety:
        problems += [f"{mw} missing from docs/safety.md (the safety plan lists every required warning)"
                     for mw in sorted(registered) if mw not in safety]
    if problems:
        return Result("manual-warnings", FAIL, f"{len(problems)} manual warning problem(s)", problems)
    return Result("manual-warnings", PASS, f"{len(registered)} manual warning(s) registered in STATUS.md")


@register(
    "manual",
    "docs/manual.md: required sections, target-market rules (STATUS.md markets:), every MW-xx warning referenced",
)
def check_manual(project: Project, opts: dict) -> Result:
    from ohmai.status import load

    rel = "docs/manual.md"
    text = _read(project, rel)
    if not text:
        return Result("manual", FAIL, f"{rel} missing (copy it from the OhmAI template)")
    if MARKER in text:
        return Result("manual", FAIL, f"{rel} still has the template marker; fill it in, then delete the marker line")
    st = load(project.root / "STATUS.md")
    problems: list[str] = []
    have = _sections(text)
    problems += [f"missing section '## {s.title()}'" for s in SECTIONS if s not in have]
    todos = [ln.strip() for ln in text.splitlines() if "TODO" in ln]
    problems += [f"TODO line: {t}" for t in todos]
    warnings = set(st.manual_warnings) | set(_doc_warnings(project))
    problems += [f"warning {mw} not referenced in {rel}" for mw in sorted(warnings) if mw not in text]
    markets = st.markets
    notes: list[str] = []
    if not markets:
        problems.append("STATUS.md front matter has no target markets (markets: [EU, ...]); set them in phase 1")
    for m in markets:
        rules = MARKET_RULES.get(m)
        if rules is None:
            notes.append(f"no built-in rules for market {m}: the phase 3 safety plan must list them; review by hand")
            continue
        for what, rx, src in rules:
            if not re.search(rx, text, re.I):
                problems.append(f"{m}: {what} not found ({src})")
    if problems:
        return Result("manual", FAIL, f"{len(problems)} problem(s) in {rel}", problems + notes)
    if notes:
        return Result("manual", WARN, f"{rel} complete for built-in markets; {len(notes)} market(s) need manual review",
                      notes)
    return Result("manual", PASS, f"{rel}: {len(SECTIONS)} sections, markets {', '.join(markets)}, "
                  f"{len(warnings)} warning(s) referenced")
