# Golden projects

Each directory here is a reference project. `ohmai golden` rebuilds it from its sources with the pinned
toolchain and diffs the outputs against the approved snapshots in `snapshots/`. See docs/TESTING.md.

| golden | what | artefacts |
|---|---|---|
| ldo-led | 2-pin header → AP2112K-3.3 LDO → 3V3 → R + LED; 2 sheets drawn by `ohmai schematic build` from `src/hardware/gen/layout/*.yaml`. Passive/connector MPNs are FICTIONAL (`FICT-`); the AP2112 constraint cites Diodes DS39724 Rev. 2-2. | netlist, erc, bom, symbols, checks |

No golden has a layout yet, so drc and gerbers are not snapshotted (the artefacts exist in
`ohmai/golden.py`; add them to `golden.yaml` once a golden has a routed `.kicad_pcb`).

## pico-k (planned, once frozen)

pico-k (`../pico-k`) becomes a golden project when its owner freezes a revision. It is
not copied here and is never modified by ohmai: its `golden.yaml` points at the project repo and a frozen
git ref, and the run builds from `git archive <ref>` into a temporary directory:

```yaml
# tests/golden/pico-k/golden.yaml  (add when pico-k is frozen; not present yet)
name: pico-k
source: ../pico-k   # or $PICO_K_DIR
ref: <frozen tag, e.g. rev-a-frozen>
needs: [kicad-cli, kicad-libs]
build: [kicad-libs-tables]           # its schematic is hand-drawn: no `schematic` step
artefacts: [netlist, erc, bom, symbols, checks, drc, gerbers]
checks: [erc, netlist, pin-map, single-source, passives, pullups, i2c-addr, bom-lock, drc]
```

Then `ohmai golden pico-k --accept --reason "pico-k <ref> frozen"` records the first snapshot.

## Rules

- A difference fails the run. Read the diff. Accept only when the change is intended or explained:
  `ohmai golden <name> --accept --reason "..."`. The reason, the changed artefacts and the toolchain are
  logged in `snapshots/MANIFEST.yaml`. Commit the snapshots on their own.
- Fictional data stays marked fictional (`FICT-` MPNs, FICTIONAL in READMEs).
