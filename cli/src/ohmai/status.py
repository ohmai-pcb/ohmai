# SPDX-License-Identifier: Apache-2.0
"""Parse STATUS.md: YAML front matter + 'Locked decisions' table + 'Open questions' checklist."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ohmai.phases import BY_NUM, resolve


@dataclass
class Status:
    meta: dict = field(default_factory=dict)
    decisions: list[dict] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    closed_questions: list[str] = field(default_factory=list)
    manual_warnings: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def markets(self) -> list[str]:
        """Target markets from the front matter, e.g. [EU, UK]; [] when not decided."""
        m = self.meta.get("markets") or []
        if isinstance(m, str):
            m = [x for x in re.split(r"[,\s]+", m) if x]
        return [str(x).strip().upper() for x in m]

    @property
    def phase(self):
        p = self.meta.get("phase")
        return BY_NUM.get(p) if isinstance(p, int) else resolve(str(p or ""))

    def as_dict(self) -> dict:
        ph = self.phase
        return {
            **self.meta,
            "phase_title": ph.title if ph else None,
            "decisions": self.decisions,
            "open_questions": self.open_questions,
            "markets": self.markets,
            "manual_warnings": self.manual_warnings,
            "problems": self.problems,
        }


def _sections(body: str) -> dict[str, str]:
    out, cur = {}, None
    for line in body.splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            cur = m.group(1).strip().lower()
            out[cur] = ""
        elif cur is not None:
            out[cur] += line + "\n"
    return out


def _table(text: str) -> list[dict]:
    rows = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("|")]
    if len(rows) < 2:
        return []
    cells = lambda r: [c.strip() for c in r.strip("|").split("|")]  # noqa: E731
    head = [h.lower() for h in cells(rows[0])]
    out = []
    for r in rows[2:]:
        c = cells(r)
        if any(c) and not all(x.startswith("<!--") or not x for x in c):
            out.append(dict(zip(head, c, strict=False)))
    return out


def parse(text: str) -> Status:
    st = Status()
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if m:
        try:
            st.meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as e:
            st.problems.append(f"front matter is not valid YAML: {e}")
        body = text[m.end():]
    else:
        st.problems.append("no YAML front matter (--- ... ---) at the top of STATUS.md")
    for key in ("project", "ohmai_version", "phase", "gate"):
        if key not in st.meta:
            st.problems.append(f"front matter missing '{key}'")
    if st.meta.get("gate") not in (None, "open", "in_review", "approved"):
        st.problems.append(f"gate must be open | in_review | approved, not {st.meta.get('gate')!r}")
    if "phase" in st.meta and st.phase is None:
        st.problems.append(f"unknown phase {st.meta.get('phase')!r} (use 1-12)")
    sec = _sections(body)
    st.decisions = _table(sec.get("locked decisions", ""))
    for line in sec.get("open questions", "").splitlines():
        q = re.match(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$", line)
        if q:
            (st.closed_questions if q.group(1).lower() == "x" else st.open_questions).append(q.group(2).strip())
    for line in sec.get("manual warnings", "").splitlines():
        w = re.match(r"^\s*[-*]\s+(?:\*\*)?(MW-\d{2,})(?:\*\*)?[:\s]\s*(.*)$", line)
        if w:
            st.manual_warnings[w.group(1)] = w.group(2).strip()
    if "markets" in st.meta and not isinstance(st.meta["markets"], (list, str, type(None))):
        st.problems.append("front matter 'markets' must be a list, e.g. [EU, UK]")
    return st


def load(path: Path) -> Status:
    if not path.is_file():
        st = Status()
        st.problems.append(f"{path} not found")
        return st
    return parse(path.read_text(encoding="utf-8"))


def set_front(path: Path, phase: str | None = None, gate: str | None = None, decision: str | None = None,
              today: str | None = None) -> list[str]:
    """Set `phase` / `gate` in STATUS.md front matter in place (comments and other keys kept). Returns the changes.

    Agents use this instead of hand-editing YAML. `--gate approved` records an owner approval, so it needs the
    decision id that logs it, and that id must already have its own entry in docs/decisions.md.
    """
    import datetime

    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        raise ValueError("no YAML front matter in STATUS.md")
    fm, changes = m.group(1), []
    if gate is not None and gate not in ("open", "in_review", "approved"):
        raise ValueError(f"gate must be open | in_review | approved, not {gate!r}")
    if gate == "approved":
        if not decision or not re.fullmatch(r"D-\d{3}", decision):
            raise ValueError("--gate approved needs --decision D-xxx (the owner approval you recorded)")
        log = path.parent / "docs" / "decisions.md"
        if not log.is_file() or not re.search(rf"^\s*(?:\|\s*|#+\s*){decision}\b", log.read_text(encoding="utf-8"), re.M):
            raise ValueError(f"{decision} has no entry in docs/decisions.md; record the owner's decision first")

    def put(fm: str, key: str, val: str) -> str:
        pat = re.compile(rf"^({key}:[ \t]*)([^#\n]*?)([ \t]*(#.*)?)$", re.M)
        if pat.search(fm):
            old = pat.search(fm).group(2).strip()
            if old != val:
                changes.append(f"{key}: {old} -> {val}")
            return pat.sub(lambda mm: f"{mm.group(1)}{val}{mm.group(3)}", fm, count=1)
        changes.append(f"{key}: (none) -> {val}")
        return fm + f"\n{key}: {val}"

    if phase is not None:
        ph = resolve(str(phase))
        if ph is None:
            raise ValueError(f"unknown phase {phase!r} (1-12 or a phase id)")
        fm = put(fm, "phase", str(ph.num))
    if gate is not None:
        fm = put(fm, "gate", gate)
    if changes:
        fm = put(fm, "updated", today or datetime.date.today().isoformat())
        path.write_text(text[:m.start(1)] + fm + text[m.end(1):], encoding="utf-8")
    return changes
