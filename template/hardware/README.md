<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
# hardware/

KiCad 9 project: `{{project_slug}}.kicad_pro`, `.kicad_sch` (root sheet), `.kicad_pcb`.

- Phase 7: the root sheet holds only hierarchical sheets, **one functional block per sheet**
  (`power_in.kicad_sch`, `regulator.kicad_sch`, ...).
- Symbols and footprints come from KiCad's official libraries or `lib/` (project library, reviewed
  in a PR). Never generate them.
- Put each part's MPN in a symbol field named `MPN` so checks can match `parts/constraints/`.
- Phase 8: fab design rules go in `{{project_slug}}.kicad_dru` (fab capabilities plus creepage from `docs/safety.md`).
- `release.kibot.yaml` holds the KiBot outputs for tagged releases (CI only).
