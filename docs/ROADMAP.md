# Roadmap

Versions apply to the tool (CLI + skill + template), all versioned together.

## v0.1: skeleton (now)
- Skill + 12 phase guides, project template, Apache-2.0.
- CLI: `new`, `status`, `check`, `release --dry-run`, `skill install`.
- Working: STATUS.md parsing, doc-marker checks, constraints schema validation, sheet-load, ERC, DRC,
  netlist diff, pin-map, single-source, passives (C/R/L presence + value range), pull-ups, I2C uniqueness,
  EVB-compare presence, PNG render, BOM schema/lock/diff (fab actual-purchase + power-rating downgrade),
  RFQ standalone lint, fab export via kicad-cli.
- Stubs: creepage (TODO), abs-max vs net voltage (not started), assembly drawings, STEP in `release`.
- Tool quality (2026-10-15): two-tier toolchain lock (`ohmai lock`, `check toolchain`, `ohmai upgrade`),
  known-bad mutation suite (a case per check; creepage has none until it is implemented), golden projects
  (ldo-led), weekly canary. Optional research-tool integrations: `skidl-netlist` (SKiDL, separate process),
  `ohmai parts search` (jlcparts DB). See docs/TESTING.md, docs/ARCHITECTURE.md.

## v0.2: first real project end to end
- Dogfood on one real board through rev-A. Fix every friction point in the phase guides.
- Template CI verified on GitHub (kicad/kicad:9.0 container, sticky comment, KiBot release).
- pico-k frozen at rev-A and added as a golden project by reference (with layout: drc + gerbers artefacts).
- First canary run on GitHub; first real `ohmai upgrade` (kicad-cli 9.0.x) through the suites.
- Publish `ohmai` to PyPI. `ohmai new --github` (owner-approved `gh repo create`).
- `ohmai status --set-phase/--gate` so agents do not hand-edit the front matter.

## v0.3: design-intent checks
- Net voltage annotations (from the power tree) → abs-max and capacitor/resistor rating checks per pin.
- Creepage/clearance: generate `.kicad_dru` rules from the `docs/safety.md` table and verify them
  (plus its known-bad case).
- A golden board with a routed layout, so drc and gerbers are snapshotted and Freerouting/KiKit upgrades get
  suite coverage.
- Readability lint for schematics (crossings, 4-way junctions, hidden refs, label overlap).
- Schematic visual diff (KiBot/KiRi style) posted in PRs.

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
