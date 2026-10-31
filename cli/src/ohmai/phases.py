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
    Phase(7, "schematic", "Schematic", ("kicad-app", "kicad-libs", "toolchain", "sheet-load", "erc", "netlist", "skidl-netlist", "pin-map", "single-source", "passives", "pullups",
           "i2c-addr", "evb-compare", "readability", "render", "review:schematic"),
          "Human reviews the schematic diff"),
    Phase(8, "layout", "Layout", ("kicad-app", "toolchain", "footprints", "escape-room", "drc", "creepage", "review:layout"), "3D view, 1:1 print, pin-1/rotation check"),
    Phase(9, "enclosure", "Enclosure / IP", ("docs:docs/enclosure.md",), "Owner approves fit and IP"),
    Phase(10, "bom", "BOM lock", ("bom-lock", "bom-diff", "review:bom"), "Owner approves the fab's actual-purchase list"),
    Phase(11, "fab", "Fab outputs", ("kicad-app", "toolchain", "erc", "drc", "bom-lock", "fab-outputs", "review:fab"), "Owner human pre-order sign-off & release tag"),
    Phase(12, "bringup", "Bring-up & test", ("docs:test/bringup-plan.md", "bringup-code", "manual", "review:bringup"), "Owner signs off results"),
)

# Every phase also checks commit trailers (`commits`, see ohmai/trailers.py), before its review gate.
PHASES = tuple(Phase(p.num, p.id, p.title,
                     tuple(c for c in p.checks if not c.startswith("review:")) + ("commits",)
                     + tuple(c for c in p.checks if c.startswith("review:")), p.gate) for p in PHASES)

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
