"""The 12 OhmAI phases and the checks each one runs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Phase:
    num: int
    id: str
    title: str
    checks: tuple[str, ...]
    gate: str


PHASES: tuple[Phase, ...] = (
    Phase(1, "requirements", "Requirements", ("docs:docs/requirements.md", "questions"), "Owner locks the spec"),
    Phase(2, "architecture", "Architecture & trade study", ("docs:docs/architecture.md", "questions", "manual-warnings", "mcu-pin-budget"),
          "Owner picks an architecture"),
    Phase(3, "safety", "Safety & compliance plan", ("docs:docs/safety.md", "questions", "manual-warnings", "review:safety"), "Owner approves the plan"),
    Phase(4, "parts", "Part selection & costed BOM", ("docs:parts/selection.md", "bom-schema", "mcu-pin-budget"),
          "Owner approves parts with a named second source"),
    Phase(5, "constraints", "Per-part constraints", ("constraints", "mcu-pin-budget", "review:constraints"), "Independent review of every constraints file + owner approval"),
    Phase(6, "rfq", "Supplier questions / RFQs", ("rfq",), "Owner sends the RFQs"),
    Phase(7, "schematic", "Schematic", ("kicad-app", "kicad-libs", "lib-refs", "toolchain", "sheet-load", "erc", "netlist", "gen-netlist", "wire-ends", "skidl-netlist", "pin-map", "single-source", "passives", "passive-mpn", "pullups",
           "i2c-addr", "evb-compare", "readability", "render", "cross-sheet", "review:schematic"),
          "Human reviews the schematic diff"),
    Phase(8, "layout", "Layout", ("kicad-app", "toolchain", "lib-refs-strict", "footprints", "escape-room", "drc", "creepage", "review:layout"), "3D view, 1:1 print, pin-1/rotation check"),
    Phase(9, "enclosure", "Enclosure / IP", ("docs:docs/enclosure.md",), "Owner approves fit and IP"),
    Phase(10, "bom", "BOM lock", ("bom-lock", "bom-diff", "lib-refs-strict", "review:bom"), "Owner approves the fab's actual-purchase list"),
    Phase(11, "fab", "Fab outputs", ("kicad-app", "toolchain", "erc", "drc", "bom-lock", "fab-outputs", "review:fab"), "Owner human pre-order sign-off & release tag"),
    Phase(12, "bringup", "Bring-up & test", ("docs:test/bringup-plan.md", "bringup-code", "manual", "review:bringup"), "Owner signs off results"),
)

# Every phase also checks commit trailers (`commits`, see ohmai/trailers.py) and that every cited decision id is in
# docs/decisions.md (`decisions`), before its review gate.
PHASES = tuple(Phase(p.num, p.id, p.title,
                     tuple(c for c in p.checks if not c.startswith("review:")) + ("commits", "decisions")
                     + tuple(c for c in p.checks if c.startswith("review:")), p.gate) for p in PHASES)

# Per-sheet gate (`ohmai check schematic --sheet <name>`): enough to commit one sheet. In order: load before
# ERC (an ERC on a sheet that did not load is invalid), then netlist + generator intent + wires, pin map,
# passives, readability, render. The project-wide schematic gate is every sheet passing this, plus the
# project-only checks (toolchain, libraries, single-source, pull-ups, I2C, EVB compare, cross-sheet) and
# the separate review.
SHEET_CHECKS: tuple[str, ...] = ("sheet-load", "erc", "netlist", "gen-netlist", "wire-ends", "pin-map", "passives", "passive-mpn",
                                 "readability", "render")

BY_ID = {p.id: p for p in PHASES}
BY_NUM = {p.num: p for p in PHASES}


def resolve(name: str) -> Phase | None:
    """'schematic', '7', '07', '07-schematic' -> Phase."""
    n = name.lower().strip()
    if n in BY_ID:
        return BY_ID[n]
    head = n.split("-", 1)[0]
    if head.isdigit() and int(head) in BY_NUM:
        return BY_NUM[int(head)]
    return None
