"""Locate the project template and the skill (repo checkout or installed wheel)."""

from __future__ import annotations

import os
from pathlib import Path

_PKG = Path(__file__).resolve().parent


def _candidates(name: str) -> list[Path]:
    env = os.environ.get(f"OHMAI_{name.upper()}_DIR")
    out = [Path(env)] if env else []
    out.append(_PKG.parents[2] / name)  # editable install: <repo>/cli/src/ohmai -> <repo>/<name>
    out.append(_PKG / "_data" / name)  # wheel install
    return out


def data_dir(name: str) -> Path:
    for p in _candidates(name):
        if p.is_dir():
            return p
    raise FileNotFoundError(
        f"OhmAI {name}/ not found (looked in {', '.join(map(str, _candidates(name)))}). "
        f"Set OHMAI_{name.upper()}_DIR or reinstall."
    )


def template_dir() -> Path:
    return data_dir("template")


def skill_dir() -> Path:
    return data_dir("skill")


def tests_dir() -> Path:
    """Known-bad fixtures and golden projects (<repo>/tests, or ohmai/_data/tests in a wheel)."""
    return data_dir("tests")


def constraints_schema_path() -> Path:
    return template_dir() / "parts" / "constraints.schema.json"
