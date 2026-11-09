# SPDX-License-Identifier: Apache-2.0
"""Project discovery and config (ohmai.yaml at the project root)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG = "ohmai.yaml"


class NotAProject(Exception):
    pass


@dataclass
class Project:
    root: Path
    config: dict = field(default_factory=dict)

    def path(self, key: str, default: str) -> Path:
        cur: object = self.config
        for part in key.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        return self.root / (cur if isinstance(cur, str) and cur else default)

    @property
    def schematic(self) -> Path:
        return self.path("hardware.schematic", "hardware/project.kicad_sch")

    @property
    def pcb(self) -> Path:
        return self.path("hardware.pcb", "hardware/project.kicad_pcb")

    @property
    def expected_nets(self) -> Path:
        return self.path("expected_nets", "expected_nets.csv")

    @property
    def constraints_dir(self) -> Path:
        return self.path("constraints_dir", "parts/constraints")

    @property
    def bom(self) -> Path:
        return self.path("bom", "bom/bom.csv")

    @property
    def out(self) -> Path:
        p = self.path("out_dir", "out")
        p.mkdir(parents=True, exist_ok=True)
        return p


def find_project(start: Path | None = None) -> Project:
    cur = (start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        cfg = d / CONFIG
        if cfg.is_file():
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            return Project(root=d, config=data)
    raise NotAProject(f"no {CONFIG} found in {cur} or its parents (run `ohmai new` first)")
