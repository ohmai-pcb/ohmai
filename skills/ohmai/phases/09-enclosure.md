<!-- SPDX-License-Identifier: Apache-2.0 -->
# Phase 9: Enclosure and IP (if relevant)

**Goal:** the board fits, mounts, seals and cools in its enclosure. Branch `phase/enclosure`.
Check `ohmai check enclosure`. With no enclosure, replace the template marker in `docs/enclosure.md`
with an HTML comment that starts `ohmai:not-applicable`, plus the reason, and close the phase.

## Do
1. Pick or specify the enclosure (MPN or drawing), with cut-outs, bosses and height keep-outs.
2. Export STEP (`kicad-cli pcb export step`) and check the fit in the enclosure model.
3. IP rating: seals, connector ratings, venting, condensation. Thermal path from the hot parts to the outside.
4. Feed any changes back into layout (a `fix/` branch and PR).

## Gate
The owner approves fit and IP.
