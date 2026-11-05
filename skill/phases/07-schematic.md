# Phase 7: Schematic

**Goal:** a schematic that matches the architecture, the constraints and the expected nets.
Branch `phase/schematic` (one PR per sheet is fine). Default check: `ohmai check schematic --sheet <name>`; whole-project checks at integration milestones.

## Bounded work: reuse, diagnose, verify
Follow skill section 2's systematic fix loop and retro/rationalization table. Write a mini plan per dense
sheet: goal, placement commit, wiring commit, exact gates, named escapes. Use the first sufficient rung:
1. Need it? Skip speculative generator features.
2. Reuse OhmAI/project: `--sheet`, label policy, split sheet, `schematic build` + YAML.
3. Use KiCad/generator primitives: explicit ends, per-sheet UIDs, split at pins/junctions/labels.
4. Use an installed allowed tool within subprocess/CI rules (no GPL imports).
5. Make one clear symbol/wire move or policy-allowed label edit.
6. Only then minimum new code that passes all gates.

No maze/BFS/private router, search, or generator-local `check()` hill-climb on schematic/adopt work.
Preview is not the gate. A crossing gets one explicit edit then a real gate; a wire longer than its block
means move the part. If a scratch script's fix is not in the project generator after one run, the attempt
is spent. Never cut validation, safety, constraints, datasheet calibration, ERC/DRC or readability.
Before a fix: reproduce/quote check + object → investigate root cause → one written hypothesis → one
minimal edit → rerun the same gate and record output + exit. No progress/completion claim without fresh
gate evidence, even for `wip:` (report blocked FAIL/SKIP honestly).

**Placement before wires:** commit symbols, ties and values only after verifying intended pin-to-net
assignments (explicitly mark this incomplete drawing `wip:` and retain fresh failing gate evidence).
Then add explicit wires in a second commit only after `ohmai check schematic --sheet <name>` passes.
A placement-only drawing is not a passing sheet.

**Stop outranks unbounded redraw:** third identical signature exits **3** (FAIL **1**, PASS **0**);
`out/check-attempts.json` keeps commands/results. PASS or changed signature resets it; never game the
counter. Also stop after three drawing attempts without a real gate, or fixes that keep re-breaking
checks. Next deliverable is retro + `wip:` placement, not search #4. Copy skill section 2's retro:
signature, three commands/changes/exits, failed hypothesis, process trap, proposed OhmAI change.
Choose split sheet, rail-and-label or one symbol move; record a distinct approach in STATUS. Owner only
for ambiguous design intent/product trade-offs, not every dense sheet. Readability is still a hard gate,
never `shortcut:` debt. End every stop/gate: `Skipped / not checked: …; residual risk: …`.

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
- Exception (adopted repos, generator-embedded symbols): with `checks.lib_refs.allow_embedded: true` a symbol
  that lives only in the sheet's embedded cache passes `lib-refs` **only** when a constraints file binds it
  (`match.lib_symbol`), so `pin-map` checks every pin against the datasheet table. No constraints file: FAIL.
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
- Root sheet plus **one functional block per sheet**. Labels are tools, not banned: hierarchical (or global)
  labels carry nets between sheets; local labels may join blocks inside a sheet. The hard rule from
  dogfooding: **passives wired to their IC pins, labels between blocks/sheets, power symbols for rails.**
  `checks.readability.labels`: `allowlist` (default; local labels only by name in `allow_local_labels`) or
  `rail-and-label` (local labels free; the passive-to-IC wire path stays a FAIL). Pick it in Phase 7 yourself;
  it is not an owner question.
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
  touching a power symbol's name, and titles longer than the title-block field (ESTIMATE of the font). Renders of the datasheet application figures
  are an optional reference for the reviewer, not a gate; 0 readability problems is the gate.

## Readability is a hard gate (not debt)
- `ohmai check readability` must PASS with **0 problems** before the review and the owner gate. It is part of
  `review:schematic` (that check FAILs while readability FAILs) and is not waivable, not "accepted debt", and
  never an owner-decision item. Fix the drawing; do not loosen `checks.readability` allow-lists to pass.
- Human-readable means: one functional block per sheet; circuits grouped (IC with its passives around it, like the
  datasheet figure); signal flow left to right, power top, ground bottom; real orthogonal wires for every
  local connection; hierarchical labels only at the sheet edge for nets that leave the sheet; aligned rails and
  even spacing; no text on wires, pins or other text. A grid of parts joined only by net labels is not a schematic.
- **Common minimal sheet size:** root and every sub-sheet use the same paper size (`paper:` in each
  `hardware/gen/layout/*.yaml`), the smallest standard size (A4 before A3 before A2) on which every sheet
  fits cleanly. Mixed sizes FAIL `readability`. Record the chosen size in STATUS.md.
- Background: the previous project (pico-k) required a clean readability pass; a dogfood board first reached its
  review gate with 167 readability problems recorded as debt. That was a skill/process failure, now closed.

## Per-sheet gate, then the project gate
Draw and close **one sheet at a time**: `ohmai check schematic --sheet <name>` (name, path `/1-power/`, file or
stem; `root` for the root) runs, scoped to that sheet: sheet-load, erc, netlist, gen-netlist, wire-ends,
pin-map, passives, readability, render. The sheet is done when that RESULT is PASS. The project gate is then
`ohmai check schematic`: every sheet passing **plus** `cross-sheet` (every net that leaves a sheet has an exact
pin in `expected_nets.csv` on each sheet it reaches) **plus** the separate `review:schematic`.

Ordered rules (do not reorder, do not skip):
1. LOAD before ERC. A 0-violation ERC on a sheet that did not load, or on 0 placed symbols, is a FAIL
   (`erc` reports "ERC result invalid" unless `sheet-load` passed and ERC covered every sheet instance).
2. Hold the pin-to-net map: `expected_nets.csv` lists every multi-node net, **unlabeled ones included**
   (`Net-(...)` with no row FAILs `netlist`); `gen-netlist` compares the generator's intent pin by pin with
   the KiCad export (split, merged, missing, unconnected, NC).
3. No truncated wires: `wire-ends` FAILs an end that touches nothing and a pin sitting mid-wire (it looks
   connected in the render but is not; a wire must end on the pin or on a junction).
   Generator contract (including foreign generators): split **every segment at every pin, junction and
   label**. A pin on a segment interior is FAIL, not proof of connectivity, even when another segment
   ends at that pin. Current `wire-ends` does not catch every foreign-segment/interior-tee case; do not
   infer full geometric correctness from PASS (ROADMAP follow-up). `gen-netlist` is pin→net intent only,
   not a comparison of geometric endpoints.
4. Convergence: when a drawing step does not converge (the same check fails 3 times after fixes, or a
   fix re-breaks another check), **stop and report** with the failing check output; do not keep redrawing.

## Checks, in this order (`ohmai check schematic` runs them)
0. `kicad-app`: `kicad-cli version` is inside ohmai.yaml `kicad.app` (e.g. `">=9.0.2,<9.1"` or `"9.0.x"`);
   FAIL outside the range, WARN when not pinned. Also run in phases 8 and 11.
   `kicad-libs`: pinned library version installed, project lib tables point at it.
   `toolchain`: installed tools vs `toolchain.lock.yaml` (`ohmai lock` once, commit it). Tier 1 (KiCad,
   libraries, Freerouting, KiKit, KiBot, iBOM, kipy, SKiDL, jlcparts, Python deps) must equal the lock; move a pin only with
   `ohmai upgrade <tool> <version>`. kicad-app/kicad-libs also FAIL when they differ from the lock.
1. `sheet-load`: every sheet loads and the set has placed symbols (records `out/load/result.json`). ERC
   silently drops a sheet that fails to load, so this runs first.
2. `erc`: kicad-cli ERC, all severities. Never silence it. Invalid (FAIL) without a valid load record.
3. `netlist`: netlist against `expected_nets.csv` (missing, split, merged, extra, name, nc). A net wired
   inside one block without a label (KiCad name `Net-(...)`) passes the name rule; its nodes still must match,
   and it needs a row (unlabeled nets are counted; one without a row FAILs). A named net without a row WARNs.
   `gen-netlist`: generator intent (`hardware/gen/layout/*.yaml` nets, or `checks.gen_netlist.intent` CSV
   `ref.pin,net` for a foreign generator) vs the KiCad netlist. `wire-ends`: see ordered rule 3.
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
11b. `cross-sheet`: project gate only (above). FAIL with `checks.cross_sheet_strict: true` (template), else WARN.
12. `commits`: every commit on the branch carries an `OhmAI-Author:` trailer (skill section 3a).
The review gate `review:schematic` is reported separately (not in RESULT) so the work can pass and be
committed before the review exists; `--gate` counts it.

Commit after each pass. Same error 3 times: stop and report (ordered rule 4).

## Adopting a foreign repo
`ohmai adopt` in an existing KiCad repo writes `ohmai.yaml` and an `expected_nets.csv` baseline from the
current netlist (never overwriting: an existing file gets `*.adopted.*` beside it). The baseline is the
current design, not verified intent: check rows against datasheets (`pin-map`) before trusting them.
Checks that cannot apply go in `checks.na: {<check>: <reason>}`; they report `SKIP N/A: <reason>`, an empty
reason FAILs. Minimum without the command: the same two files by hand, plus the N/A map.
Climb the six-rung ladder above before adding generator helpers; private routers and local `check()`
hill-climbs remain forbidden on adopt. Default to per-sheet gates, then integrate all sheets.

Distinguish **library missing** (resolve/import vetted libraries via project tables) from **pins unverified**
(add datasheet-backed constraints, not just an embedded cache). For `checks.lib_refs.allow_embedded: true`,
put a non-example YAML in `parts/constraints/` with `match.lib_symbol` using the symbol item name (the
form matched by `pin-map`), plus `match.refs` or `match.mpn` when needed. Include the datasheet source,
revision and cited pages, every pin number/name/direction with cites, NC intent and required externals;
validate against the constraints schema and run `constraints` + per-sheet `pin-map` on real exported pins.
The `lib-refs` binding PASS alone does **not** certify pin correctness; pin-map must verify it.
Review reuse before inventing: could `--sheet`, label policy or one placement edit have sufficed?

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

## Toolchain loss between turns
If `kicad-cli`, the ohmai venv or `out/` vanish, the shared box lost machine-local state; nothing was
uninstalled. Run `scripts/restore-toolchain.sh` (one agent; others wait on its lock), `source
~/ohmai-venv/env.sh`, then rebuild with `ohmai schematic build` and re-run checks. Commit the regenerated
sheets, `gen/layout/` and `hardware/lib/` before ending each turn, so a wipe never loses work.
