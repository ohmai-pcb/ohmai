# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Validate the fields we use against documented host contracts, not a host emulator.

Schemas are our strict subset of public documentation, not official host schemas.
See docs/PLUGIN-PACKAGING.md for sources and limitations.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ("plugin.json", ".grok-plugin/plugin.json", ".claude-plugin/plugin.json", ".cursor-plugin/plugin.json")


def validate(root: Path, tag: str | None = None) -> str:
    root = root.resolve()
    schema = json.loads((ROOT / "docs/plugin-schemas/plugin.schema.json").read_text())
    manifests = [json.loads((root / name).read_text()) for name in MANIFESTS]
    for manifest in manifests:
        jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(manifest)
    if not all(manifest == manifests[0] for manifest in manifests):
        raise ValueError("host manifests have drifted")
    version = manifests[0]["version"]
    if tag and tag != f"v{version}":
        raise ValueError("release tag and plugin version disagree")
    skill = root / "skills/ohmai/SKILL.md"
    front = yaml.safe_load(skill.read_text().split("---", 2)[1])
    if front.get("license") != "Apache-2.0":
        raise ValueError("skill license must remain Apache-2.0")
    if front["name"] != "ohmai" or front["metadata"]["version"] != version:
        raise ValueError("canonical skill and plugin version disagree")
    for word in ("OhmAI", "KiCad", "PCB", "hardware idea", "ohmai.yaml", "Grok Bot", "Grok Build"):
        if word not in front["description"]:
            raise ValueError(f"skill description must include {word}")
    if (root / "cli").exists():
        module = (root / "cli/src/ohmai/__init__.py").read_text()
        match = re.search(r'__version__\s*=\s*[\'"]([^\'"]+)', module)
        if not match or match[1] != version:
            raise ValueError("CLI and plugin version disagree")
        if not (root / "skill").is_symlink() or (root / "skill").resolve() != skill.parent:
            raise ValueError("legacy skill must point at the canonical tree")
        # Actual GPL imports in the core process are forbidden. The pcbnew script
        # string in native_guard.py is executed only by a separate process.
        forbidden = {"pcbnew", "easyeda2kicad", "skidl", "InSpice", "kibot", "_skidl_runner"}
        for path in (root / "cli/src/ohmai").rglob("*.py"):
            # This executable is invoked by filename in a separate interpreter.
            # It may import SKiDL; no core module may import the runner itself.
            if path.name == "_skidl_runner.py":
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                imports = ([a.name for a in node.names] if isinstance(node, ast.Import)
                           else [node.module or "", *[a.name for a in node.names]]
                           if isinstance(node, ast.ImportFrom) else [])
                if any(name.split(".")[0] in forbidden or name == "ohmai._skidl_runner" for name in imports):
                    raise ValueError(f"GPL import in Apache core: {path.relative_to(root)}")
    for host in (".grok-plugin", ".claude-plugin", ".cursor-plugin"):
        path = root / host / "marketplace.json"
        if path.exists():
            catalog_schema = json.loads((ROOT / "docs/plugin-schemas/marketplace.schema.json").read_text())
            jsonschema.Draft202012Validator(catalog_schema).validate(json.loads(path.read_text()))
    return version


def validate_marketplace(root: Path) -> str:
    for host in (".grok-plugin", ".claude-plugin", ".cursor-plugin"):
        schema = json.loads((ROOT / "docs/plugin-schemas/marketplace.schema.json").read_text())
        catalog = json.loads((root / host / "marketplace.json").read_text())
        jsonschema.Draft202012Validator(schema).validate(catalog)
        for entry in catalog["plugins"]:
            target = (root / entry["source"]).resolve()
            if root.resolve() not in target.parents or not target.is_dir():
                raise ValueError("catalog source escapes marketplace or is missing")
            validate(target)
    return validate(root / "plugins/ohmai")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, nargs="?", default=ROOT)
    parser.add_argument("--marketplace", action="store_true")
    parser.add_argument("--tag", help="expected vX.Y.Z (does not create or publish a tag)")
    parser.add_argument("--release", action="store_true", help="also require an exact HEAD tag and resolved NOTICE")
    args = parser.parse_args()
    version = validate_marketplace(args.path) if args.marketplace else validate(args.path, args.tag)
    if args.release:
        if "TODO" in (ROOT / "NOTICE").read_text():
            parser.error("NOTICE copyright holder TODO must be resolved by the owner")
        tag = subprocess.run(["git", "describe", "--exact-match", "--tags", "HEAD"], cwd=ROOT,
                             text=True, capture_output=True, check=False)
        if tag.returncode or tag.stdout.strip() != f"v{version}":
            parser.error(f"release requires exact local HEAD tag v{version}; no tag was created")
    print(f"PASS documented-contract checks: ohmai {version} (native host validation still required)")


if __name__ == "__main__":
    main()
