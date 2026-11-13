# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""One canonical source in checkout; embed data once when building an sdist/wheel."""
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        bundled = root / "_resources"
        source = root.parent
        mapping = {
            "template": source / "template",
            "skill": source / "skills/ohmai",
            "tests": source / "tests",
            "LICENSE": source / "LICENSE",
            "NOTICE": source / "NOTICE",
            "pinned-tools.yaml": source / "pinned-tools.yaml",
        }
        for name, checkout in mapping.items():
            src = bundled / name if bundled.is_dir() else checkout
            if not src.exists():
                raise RuntimeError(f"missing canonical build resource: {name}")
            dest = f"_resources/{name}" if self.target_name == "sdist" else f"ohmai/_data/{name}"
            build_data.setdefault("force_include", {})[str(src)] = dest
