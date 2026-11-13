<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
# Roadmap

Versions apply to the tool (CLI + skill + template), all versioned together.

## v0.1.0: first release (2026-11-03)
Everything below ships in 0.1.0.
- Skill + 12 phase guides, project template, Apache-2.0.
- CLI: `new` (`--anonymous`), `adopt`, `status` (+ `--set-phase/--gate/--decision`), `check` (per phase,
  per sheet `--sheet`, `--gate`), `review start`, `commit`/`hooks`, `release` (`--dry-run`), `skill install`,
  `schematic build`, `layout {fit,fanout,autoroute,pour,handroute}`, `passives swap`, `datasheets`,
  `kicad-libs` (pinned libs + agent fetch/import of missing parts), `parts search` (jlcparts), `doctor`.
- Checks: STATUS.md parsing, doc markers, decisions log, constraints schema, sheet-load, ERC, DRC, netlist
  diff, gen-netlist, wire-ends, cross-sheet, pin-map (+ unbound WARN), single-source, passives,
  passive-mpn (MPN-encoded value vs Value field), pull-ups, I2C uniqueness, EVB-compare presence,
  schematic readability lint, PNG render, BOM schema/lock/diff, RFQ lint, fab export via kicad-cli.
- Tool quality: two-tier toolchain lock (`lock`, `check toolchain`, `upgrade`), known-bad mutation suite
  (a case per check), golden project ldo-led, weekly canary workflow, optional `skidl-netlist`.
- Packaging: wheel with template + skill; `release.yml` builds it on a `v*` tag (tag == `__version__`).
  Install from git at the tag; no PyPI yet (v0.3).
- Known stubs: creepage (TODO, v0.3), abs-max vs net voltage (v0.3), assembly drawings + STEP in
  `release` (v0.4).

## Schematic geometry follow-up (#1)
- `gen-netlist` still compares pin→net names, not generator endpoint geometry. Add a separate geometric
  comparison with mutation fixtures before claiming endpoint validation; no router rewrite.
- Strengthen `wire-ends`: pins on foreign segment interiors even when another wire ends at that pin;
  unsplit interior tees/junctions/labels. Current coverage is partial. Require generators to split at all
  pins/junctions/labels now; ship stricter detection only with clean twins + known-bad cases.

## v0.2: layout tooling, plugin and public release
- Issue-feedback work collected under 0.1.1 (unreleased): GitHub issues #1/#3/#4, plugin packaging #6,
  Freerouting 2.5.0 DSN plane-layer and NPE fixes, `apply-changes` zone add/resize/delete,
  `ohmai layout current`, handroute island counts matching KiCad, NOTICE and SPDX headers.
- Brand logo and app icon in `docs/assets/logo/`, README wordmark, and a 64×64 `resources/icon.png`
  ready for KiCad PCM packaging.
- README refresh: v0.2 (unreleased) status, new layout tools, and upcoming example projects
  (pico-k, usb-c-12v-stereo, hohme-sense).
- Router guide and Freerouting run-log template (`docs/routing/`).
- `ohmai new --github` (owner-approved `gh repo create` + first push).
- CI and the weekly canary verified on GitHub (kicad/kicad:9.0 container, sticky comment, KiBot release);
  first real `ohmai upgrade` (kicad-cli 9.0.x) through the suites.

## v0.3: design-intent checks
- Dogfood a real board through rev-A; fix every friction point in the phase guides and the CLI.
- A reference board frozen at rev-A and added as a golden project (with layout: drc + gerbers), so
  Freerouting/KiKit upgrades get suite coverage.
- Multi-unit symbols (op-amps, gates, MCU banks) in `schematic build`, pin-map and gen-netlist.
- Dense QFN escape/fanout improvements.
- Net voltage annotations (from the power tree) → abs-max and capacitor/resistor rating checks per pin.
- Creepage/clearance: generate `.kicad_dru` rules from the `docs/safety.md` table and verify them
  (plus its known-bad case).
- Schematic visual diff (KiBot/KiRi style) posted in PRs.
- Publish `ohmai` to PyPI: trusted-publishing job in `release.yml` (environment `pypi`), first upload.
- passive-mpn: more vendor codes (Panasonic ERJ, KEMET, TDK C-series) and voltage/dielectric decode.

## v0.4: sourcing and outputs
- Sourcing adapters (Nexar/Octopart, LCSC, DigiKey, Mouser) that fill price/stock/lifecycle with
  source+date. Never scraped silently. (jlcparts offline search exists: `ohmai parts search`.)
- KiKit panels / fab presets as a release step (locked already).
- Fab profiles (JLC, PCBWay, Aisler, ...): DRC rules, BOM/PnP formats, actual-purchase import.
- `release`: assembly drawings with pin-1 marks, STEP, iBoM, rotation-offset table per fab.

## v0.5: datasheet assistance
- Datasheet → draft constraints file (pin table extraction with page anchors). Always `draft`, never
  auto-reviewed.
- EVB/reference-design compare helper: vendor BOM vs ours, as a table for `docs/evb-compare/`.

## v0.6-0.9: agent integration
- MCP server exposing `ohmai` checks and project state (status, open questions, gates) to any agent.
- Adapters for the KiCad IPC API (`kicad-python`) for placement helpers. Optional Freerouting for
  non-critical nets.
- Evaluation suite for agents: reuse the known-bad fixtures and goldens; agents are scored on catching the
  planted bugs (the checks already are).
- Multi-board and variants (DNP sets) support.

## v1.0: stable
- Stable template and constraints schema (`ohmai-constraints/v1` frozen), with a migration tool for
  older projects.
- Docs site, three public example projects taken through bring-up, contributor guide for new checks.
