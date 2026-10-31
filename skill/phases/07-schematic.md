# Phase 7: Schematic

**Goal:** a schematic that matches the architecture, the constraints and the expected nets.
Branch `phase/schematic` (one PR per sheet is fine). Check `ohmai check schematic`.

## Before drawing
1. Write `expected_nets.csv` from the architecture and constraints files. Cite the pin source per part
   in a comment. The owner approves changes to it in the PR diff.
2. Re-run `ohmai check mcu-pin-budget`. If the MCU nets in `expected_nets.csv` outnumber `required_nets`
   in `parts/mcu_pin_budget.yaml`, update the budget first; a shortfall sends you back to Phase 4 (owner decision).

## Libraries and symbol maps
- Pin the official KiCad libraries in `ohmai.yaml` → `kicad.libraries` (e.g. `"9.0.9.1"`), then
  `ohmai kicad-libs install` and `ohmai kicad-libs tables` (per-project `hardware/sym-lib-table` /
  `fp-lib-table`; never rely on the user's global tables). `kicad-libs` check: installed + tables match.
- Use **vetted library symbols only**: KiCad official libraries, or `hardware/lib/` after review. Never
  hand-draw or guess a symbol or footprint, and never edit one to pass a check. A part the KiCad libraries
  lack is fetched by you with `ohmai kicad-libs fetch <MPN>` (Phase 4 "Missing KiCad library parts"),
  never handed to the owner and never hand-drawn. A `provisional` part (`ohmai kicad-libs list`) may be placed
  but must be replaced by a vetted import before the schematic review gate.
- `lib-refs` (after `kicad-libs`): every schematic `lib_id` must resolve through `hardware/sym-lib-table`
  (FAIL); every schematic/BOM footprint through `fp-lib-table` (WARN here, FAIL as `lib-refs-strict` in
  layout and BOM lock). SKIP while there is no schematic and no BOM footprint.
- Library pin names or numbers differ from the datasheet? Write `parts/symbol-maps/<constraints stem>.yaml`
  (`symbol:`, `pins: {"<symbol pin>": {datasheet: "<ds pin>" | [..], symbol_name: <library name>, cite:
  {page: N}}}`). Never edit the reviewed constraints to match a symbol. Check polarity parts (LED, diode)
  against the datasheet figure and cite the page: datasheet pad 1 is often the anode, KiCad `Device:LED`
  pin 1 is K.
- Bind constraints to parts by `MPN` field (`match.mpn`) or reference (`match.refs`), never by value text.

## Drawing: datasheet-figure style
- Root sheet plus **one functional block per sheet**. Hierarchical labels only for nets that leave the
  block; long buses may use labels (allow-list). No global labels, no local labels by default.
- Draw like the datasheet's application figure: each IC with its passives **wired** (orthogonal wires on
  the 1.27 mm grid), decoupling caps next to their pins, power symbols at the part.
- Write the placement as `hardware/gen/layout/<sheet>.yaml` (+ `_root.yaml`) and draw it with
  `ohmai schematic build` (see `template/hardware/gen/layout/README.md`). Hand edits in eeschema are fine,
  but then keep the yaml in sync or drop the generator for that sheet (say so in the hardware README).
- Put each part's MPN in a symbol field `MPN`, matching `bom/bom.csv`.
- `readability` check FAILs on: a passive joined to an IC only by labels, local labels not in
  `checks.readability.allow_local_labels`, wire crossings / ambiguous junctions, overlapping text or
  symbols, wires through bodies, off-grid items, a power symbol's net name next to another node's wire or
  junction (it reads as that node's name), text on pin lines / pin numbers or over a DNP cross, a part's text
  touching a power symbol's name, and titles longer than the title-block field (ESTIMATE of the font). The reviewer judges the PNGs against the datasheet figures.

## Checks, in this order (`ohmai check schematic` runs them)
0. `kicad-app`: `kicad-cli version` is inside ohmai.yaml `kicad.app` (e.g. `">=9.0.2,<9.1"` or `"9.0.x"`);
   FAIL outside the range, WARN when not pinned. Also run in phases 8 and 11.
   `kicad-libs`: pinned library version installed, project lib tables point at it.
   `toolchain`: installed tools vs `toolchain.lock.yaml` (`ohmai lock` once, commit it). Tier 1 (KiCad,
   libraries, Freerouting, KiKit, KiBot, iBOM, kipy, SKiDL, jlcparts, Python deps) must equal the lock; move a pin only with
   `ohmai upgrade <tool> <version>`. kicad-app/kicad-libs also FAIL when they differ from the lock.
1. `sheet-load`: every sheet loads. ERC silently drops a sheet that fails to load, so this runs first.
2. `erc`: kicad-cli ERC, all severities. Never silence it.
3. `netlist`: netlist against `expected_nets.csv` (missing, split, merged, extra, name, nc). A net wired
   inside one block without a label (KiCad name `Net-(...)`) passes the name rule; its nodes still must match.
   `skidl-netlist` (optional, WARN without SKiDL): SKiDL builds a second netlist from `expected_nets.csv`
   with the pinned KiCad symbols (a pin the symbol lacks is an error) and it must equal the KiCad export
   net-by-net. SKiDL runs as a separate process (it loads GPL InSpice); `OHMAI_SKIDL_PYTHON` selects a
   separate venv.
4. `pin-map`: symbol pin numbers/names match the datasheet table (directly or via the symbol map). NC pins
   stay unconnected.
5. `single-source`: one regulator/switch/driver output per net (exceptions only in `parts/allow.yaml`,
   with a source).
6. `passives`: required external parts exist and their values are within the constraints' min/max
   (rail decoupling counted per rail, nominal-only values are a target not a maximum, neighbour nets
   reached through series parts).
7. `pullups`: open-drain and I2C pins have pull-ups.
8. `i2c-addr`: addresses are unique on each bus.
9. `evb-compare`: each power IC is compared part by part with the vendor EVB/reference
   (`docs/evb-compare/<PART>.md`).
10. `readability`: datasheet-figure style (above). Writes `out/readability.txt`.
11. `render`: PNG per sheet in `out/render/png/`. Look at every changed sheet yourself.
12. `commits`: every commit on the branch carries an `OhmAI-Author:` trailer (skill section 3a).
The review gate `review:schematic` is reported separately (not in RESULT) so the work can pass and be
committed before the review exists; `--gate` counts it.

Commit after each pass. Same error 3 times: stop and ask.

## Gate
A human reviews the PR: the schematic diff, the PNG renders, and the netlist and BOM diffs posted by CI.
Merging locks the schematic.

## Independent review
Before the gate: `ohmai review start schematic`, get an independent review at the highest feasible level (skill
section 3a), resolve every finding in `reviews/schematic/resolutions.md`; `ohmai check review:schematic` must pass.

If this gate's `min_independence` cannot be met, try every better level first and document why each was not
available; only then ask the owner for a waiver (`accepted_independence`, skill section 3a, docs/REVIEWS.md).

## Autonomous passive package swaps & standard HF decouplers
During Schematic and Layout, you may **autonomously resize standard passives** (`0402`–`0805` resistors/MLCCs and
`2016`–`3030` shielded inductors via `ohmai passives swap <REF> --package <PKG>`) and **add/co-locate standard
datasheet-recommended HF decoupling caps** (e.g., `100 nF` HF bypass next to an IC supply pin) without stopping to
ask the owner, provided `ohmai check constraints`, `erc`, `netlist`, and `passives` all pass and the update is logged
in `bom/bom.csv`, `expected_nets.csv`, and `STATUS.md`. Reserve owner prompts strictly for active ICs, connectors,
switches, crystals, board outline, or waiving a hard `R-xx` / datasheet requirement.
