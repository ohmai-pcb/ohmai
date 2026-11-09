<!-- SPDX-License-Identifier: Apache-2.0 -->
# hardware/gen/layout: datasheet-figure schematic layouts

`ohmai schematic build [--layout DIR]` reads `_root.yaml` plus one `<sheet>.yaml` per functional block and
writes `hardware/<project>.kicad_sch` and `hardware/<sheet>.kicad_sch`, using the pinned official symbols
(`ohmai kicad-libs`, embedded verbatim). Then run `ohmai check schematic --sheet <name>` (project gate at integration); `readability` judges the result
and the reviewer compares the PNGs with the datasheet application figures.

Style: each IC with its passives drawn **wired**, as in the datasheet figure. Orthogonal wires on the
1.27 mm grid; nets used on several sheets become hierarchical labels automatically; power nets become
power symbols at the pin (`power:` map). No local labels unless allow-listed in `ohmai.yaml`
(`checks.readability.allow_local_labels`) or use `checks.readability.labels: rail-and-label`;
choose the policy and record it in STATUS. Passive-to-IC connections still need wires.

## `_root.yaml`
```yaml
title: my-board
paper: A3
sheets: [power, mcu]                 # <sheet>.yaml files, in order
power: {GND: "power:GND", 3V3: "power:+3V3"}   # net -> power symbol (Value = net name)
```

## `<sheet>.yaml`
```yaml
title: Power
paper: A4
parts:                               # ref -> part; nets per symbol pin
  U1: {lib: "Regulator_Linear:AP2112K-3.3", value: AP2112K-3.3, mpn: AP2112K-3.3TRG1,
       fp: "Package_TO_SOT_SMD:SOT-23-5", nets: {"1": VIN, "2": GND, "3": VIN, "5": 3V3}}
  C1: {lib: "Device:C", value: 1u, mpn: "<MPN>", nets: {"1": VIN, "2": GND}}
  C2: {lib: "Device:C", value: 1u, mpn: "<MPN>", nets: {"1": 3V3, "2": GND}}
place:
  - {ref: U1, at: [101.6, 76.2]}                       # absolute, mm on the grid; rot 0/90/180/270, mirror x|y
  - {ref: C1, to: U1.1, at: [-10.16, 0], orient: v}    # attach to a pin: offset, wire drawn from the pin
  - {ref: C2, to: U1.5, at: [10.16, 0], orient: v, text: right}
  - {wire: [U1.1, U1.3]}                               # extra wire (optional route: [[dx, dy], ...])
  - {tap: U1.1, at: [0, -7.62]}                        # wire stub from a pin (for a port or symbol)
symbols:                                               # explicit power/port symbol spots (optional)
  - {to: U1.2, from: [0, 5.08], side: down}
flags:                                                 # PWR_FLAG where ERC needs a driver
  - {net: VIN, to: C1.1, side: up}
```
Attach keys: `to`, `at`, `orient`, `near` (which end of the part faces `to`), `pin` (which own pin meets `to`), `route`, `from` (start on an existing
wire), `first: x|y`, `rot`, `mirror`, `text: right|left|above|below|split`, `ref_at`, `value_at`,
`hide_value`. `dnp: true` marks a part not placed; `bom: false` keeps it off the BOM.
The generator splits wires at every pin and tee (KiCad joins only at wire ends) and places junctions where
three or more connections meet. Hidden NC pins get a no-connect flag.
For every generator (including foreign/adopted ones), **split segments at pins, junctions and labels**;
never rely on a mid-wire pin. It is a FAIL, not connectivity evidence. `gen-netlist` compares pin→net
names only, not generator geometry; current `wire-ends` has incomplete foreign-segment/interior-tee
coverage (see ROADMAP). Run the real per-sheet gate and inspect renders, not a private preview `check()`.
Namespace UIDs per sheet. Commit placement (symbols/ties/values, `wip:` evidence) before adding wires;
commit wiring after the per-sheet PASS. No maze/BFS/private routers or local-check hill-climbs. After
three drawing attempts or CLI exit 3, stop with a retro; use split sheet, rail-and-label or one symbol
move as a distinct approach. Never weaken gates or carry readability debt.

Keep the yamls in the review scope (`reviews.gates.schematic.scope`). If you edit a sheet by hand in
eeschema afterwards, either update the yaml or stop generating that sheet and say so in `hardware/README.md`.
