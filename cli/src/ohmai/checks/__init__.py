"""Check registry. Each check returns a Result; `ohmai check` prints them and writes out/summary.md."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ohmai.project import Project

PASS, FAIL, WARN, SKIP, TODO = "PASS", "FAIL", "WARN", "SKIP", "TODO"


@dataclass
class Result:
    name: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "summary": self.summary, "details": self.details}


CheckFn = Callable[[Project, dict], Result]
REGISTRY: dict[str, CheckFn] = {}
HELP: dict[str, str] = {
    "review": "Independent review gate: required reviews present, reviewer != author, files unchanged, issues resolved",
}


def register(name: str, help: str):
    def deco(fn: CheckFn) -> CheckFn:
        REGISTRY[name] = fn
        HELP[name] = help
        return fn

    return deco


def run(project: Project, name: str, opts: dict) -> Result:
    if name.startswith("docs:"):
        from ohmai.checks.docs import check_doc

        return check_doc(project, name.split(":", 1)[1])
    if name == "review" or name.startswith("review:"):
        from ohmai.checks.review import check_review

        return check_review(project, name.split(":", 1)[1] if ":" in name else None)
    na = (project.config.get("checks") or {}).get("na") or {}
    if name in na:
        why = str(na[name] or "").strip()
        if not why:
            return Result(name, FAIL, f"checks.na.{name} has no reason: an N/A check needs a written reason")
        return Result(name, SKIP, f"N/A: {why}")
    from ohmai import kicadlibs

    kicadlibs.activate(project)
    fn = REGISTRY.get(name)
    if fn is None:
        return Result(name, FAIL, f"unknown check '{name}'")
    try:
        return fn(project, opts)
    except FileNotFoundError as e:
        return Result(name, SKIP if "kicad-cli" in str(e) else FAIL, str(e))


# import for side effects (registration)
from ohmai.checks import (  # noqa: E402,F401
    bom,
    bringup,
    commits,
    constraints,
    decisions,
    docs,
    escape,
    gencheck,
    fab,
    layout,
    librefs,
    manual,
    mcubudget,
    pinmap,
    placement,
    readability,
    rfq,
    schematic,
    skidlnet,
    toolchain,
)
