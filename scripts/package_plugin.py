# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Build a guidance-only local marketplace; never package the repository wholesale."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("plugin.json", ".grok-plugin/plugin.json", ".claude-plugin/plugin.json", ".cursor-plugin/plugin.json", "LICENSE", "NOTICE", "INSTALL.md")


def build(source: Path, output: Path) -> Path:
    source, output = source.resolve(), output.resolve()
    if output == source or output in source.parents or source / "skills" in output.parents:
        raise ValueError("output must not replace source or sit inside the canonical skill")
    canonical = source / "skills" / "ohmai"
    if canonical.is_symlink() or not canonical.is_dir():
        raise ValueError("canonical skill must be a real directory, not a symlink")
    entries = [(source / name, Path(name)) for name in FILES]
    for src in sorted(canonical.rglob("*")):
        if src.is_symlink():
            raise ValueError(f"refusing symlink in canonical skill: {src.name}")
        if src.is_file():
            if src.suffix != ".md" or ".bak" in src.name:
                raise ValueError(f"non-guidance file in skill: {src.name}")
            entries.append((src, Path("skills/ohmai") / src.relative_to(canonical)))
    for src, name in entries:
        if src.is_symlink() or source not in src.resolve().parents or not src.is_file():
            raise ValueError(f"missing or unsafe payload input: {name}")
    # Refuse reuse rather than overwriting any untracked files.
    output.mkdir(parents=True, exist_ok=False)
    plugin = output / "plugins" / "ohmai"
    plugin.mkdir(parents=True)
    for src, name in entries:
        dst = plugin / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for host in (".grok-plugin", ".claude-plugin", ".cursor-plugin"):
        catalog = json.loads((source / host / "marketplace.json").read_text())
        catalog["plugins"][0]["source"] = "./plugins/ohmai"
        dst = output / host / "marketplace.json"
        dst.parent.mkdir(parents=True)
        dst.write_text(json.dumps(catalog, indent=2) + "\n")
    return plugin


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="NEW output directory (never overwritten)")
    args = parser.parse_args()
    print(build(ROOT, args.output))


if __name__ == "__main__":
    main()
