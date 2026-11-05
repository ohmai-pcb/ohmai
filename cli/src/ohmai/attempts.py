"""Consecutive check failure signatures; coordinates are evidence, not identity."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ohmai.checks import Result

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_PAIR = re.compile(rf"[\[(]\s*{_NUMBER}\s*,\s*{_NUMBER}\s*[\])]")
_XY = re.compile(rf"\b[xy]\s*=\s*{_NUMBER}\b", re.I)


def normalize(detail: str) -> str:
    """Keep sheet/net/pin identity, erase changing geometric locations (not pin numbers)."""
    return " ".join(_XY.sub("axis=<coord>", _PAIR.sub("(<coord>)", detail)).split())


def signature(failed: list[Result], sheet: str = "") -> str:
    keys = sorted((r.name, normalize(r.details[0] if r.details else r.summary)) for r in failed)
    return json.dumps({"sheet": sheet, "checks": keys}, sort_keys=True, separators=(",", ":"))


def record(out: Path, failed: list[Result], sheet: str, command: str) -> dict:
    """Keep only the current signature and at most its first three observed attempts.

    A PASS clears history; a changed signature starts again at one. Calls after
    the third failure still stop, so re-running the same command is not an escape.
    """
    path = out / "check-attempts.json"
    state = {}
    if failed and path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or not isinstance(state.get("attempts", []), list):
                raise ValueError("invalid history structure")
        except (ValueError, TypeError) as e:
            raise ValueError("invalid out/check-attempts.json; preserve evidence and repair history before retrying") from e
    if not failed:
        state = {"signature": None, "attempts": []}
    else:
        key = signature(failed, sheet)
        if state.get("signature") != key:
            state = {"signature": key, "attempts": []}
        if len(state["attempts"]) < 3:
            state["attempts"].append({"command": command, "changed": "", "failures": [r.as_dict() for r in failed]})
    out.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return state


def retro(state: dict) -> str:
    lines = ["STOP: third identical failure signature (exit 3). No fourth redraw.",
             "Retro / open question (copy to STATUS.md):", f"Signature: {state['signature']}"]
    for i, a in enumerate(state["attempts"], 1):
        lines.append(f"Attempt {i}: {a['command']} — changed: {a['changed'] or '____'}")
    lines += ["Hypothesis: ____", "Proposed OhmAI change (skill/check/adopt): ____",
              "Owner question: only if design intent is ambiguous; otherwise record a distinct approach.",
              "Allowed escapes: split sheet; rail-and-label; one symbol move. Record the choice in STATUS.md.",
              "Commit wip: placement + retro; readability remains a gate, stop outranks unbounded redraw.",
              "Skipped / not checked: ____; residual risk: ____"]
    return "\n".join(lines)
