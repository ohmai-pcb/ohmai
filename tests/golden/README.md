<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
# Golden projects

Each directory here is a reference project. `ohmai golden` rebuilds it from its sources with the pinned
toolchain and diffs the outputs against the approved snapshots in `snapshots/`. See docs/TESTING.md.

| golden | what | artefacts |
|---|---|---|
| ldo-led | 2-pin header → AP2112K-3.3 LDO → 3V3 → R + LED; 2 sheets drawn by `ohmai schematic build` from `src/hardware/gen/layout/*.yaml`. Passive/connector MPNs are FICTIONAL (`FICT-`); the AP2112 constraint cites Diodes DS39724 Rev. 2-2. | netlist, erc, bom, symbols, checks |

No golden has a layout yet, so drc and gerbers are not snapshotted (the artefacts exist in
`ohmai/golden.py`; add them to `golden.yaml` once a golden has a routed `.kicad_pcb`).

## External frozen board example (fictional paths)

The following `reference-board` name and path are fictional examples, not a public
repository or an included golden. Add an external board only when its owner freezes
a revision. Its `golden.yaml` points at the project repo and frozen git ref; OhmAI
builds from `git archive <ref>` into a temporary directory and does not modify the source:

```yaml
# tests/golden/reference-board/golden.yaml  (add when reference-board is frozen; not present yet)
name: reference-board
source: ../reference-board   # or $REFERENCE_BOARD_DIR
ref: <frozen tag, e.g. rev-a-frozen>
needs: [kicad-cli, kicad-libs]
build: [kicad-libs-tables]           # its schematic is hand-drawn: no `schematic` step
artefacts: [netlist, erc, bom, symbols, checks, drc, gerbers]
checks: [erc, netlist, pin-map, single-source, passives, pullups, i2c-addr, bom-lock, drc]
```

Then `ohmai golden reference-board --accept --reason "reference-board <ref> frozen"` records the first snapshot.

## Rules

- A difference fails the run. Read the diff. Accept only when the change is intended or explained:
  `ohmai golden <name> --accept --reason "..."`. The reason, the changed artefacts and the toolchain are
  logged in `snapshots/MANIFEST.yaml`. Commit the snapshots on their own.
- Fictional data stays marked fictional (`FICT-` MPNs, FICTIONAL in READMEs).
