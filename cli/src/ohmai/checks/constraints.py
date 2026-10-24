"""Per-part constraints files (parts/constraints/*.yaml): load, schema-validate, semantic checks."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema
import yaml

from ohmai.checks import FAIL, PASS, WARN, Result, register
from ohmai.project import Project
from ohmai.resources import constraints_schema_path


@lru_cache(maxsize=4)
def _schema(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def schema_for(project: Project) -> dict:
    local = project.root / "parts" / "constraints.schema.json"
    return _schema(str(local if local.is_file() else constraints_schema_path()))


def load_all(project: Project) -> tuple[dict[str, dict], list[str]]:
    """{filename: data} for every constraints file, plus load errors."""
    out, errors = {}, []
    d = project.constraints_dir
    for f in sorted(d.glob("*.y*ml")) if d.is_dir() else []:
        if ".example." in f.name:
            continue
        try:
            out[f.name] = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            errors.append(f"{f.name}: invalid YAML: {e}")
    return out, errors


def validate(data: dict, schema: dict) -> list[str]:
    v = jsonschema.Draft202012Validator(schema)
    errs = []
    for e in sorted(v.iter_errors(data), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
        errs.append(f"{loc}: {e.message}")
    pins = data.get("pins") or []
    nums = [str(p.get("number")) for p in pins if isinstance(p, dict)]
    dup = sorted({n for n in nums if nums.count(n) > 1})
    if dup:
        errs.append(f"pins: duplicate pin number(s) {', '.join(dup)}")
    return errs


@register("constraints", "Schema-validate parts/constraints/*.yaml; warn on unreviewed files or missing datasheets")
def check(project: Project, opts: dict) -> Result:
    files, errors = load_all(project)
    if not files and not errors:
        return Result("constraints", FAIL, f"no constraints files in {project.constraints_dir}")
    schema = schema_for(project)
    warns = []
    for name, data in files.items():
        errors += [f"{name}: {e}" for e in validate(data, schema)]
        if (data.get("review") or {}).get("status") != "reviewed":
            warns.append(f"{name}: review.status is not 'reviewed' (a human must review it before phase 7)")
        from ohmai.datasheets import has_datasheet_entry
        ds = (data.get("datasheet") or {}).get("file")
        if ds and not has_datasheet_entry(project, ds):
            warns.append(f"{name}: datasheet {ds} not in repo")
    summary = f"{len(files)} file(s), {len(errors)} error(s), {len(warns)} warning(s)"
    if errors:
        return Result("constraints", FAIL, summary, errors + warns)
    return Result("constraints", WARN if warns else PASS, summary, warns)
