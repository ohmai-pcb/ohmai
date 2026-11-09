<!-- SPDX-License-Identifier: Apache-2.0 -->
## Gate: phase N <name>

**Owner decision requested:** <what merging this PR locks>

### Agent checklist
- [ ] `ohmai check <phase>` passes locally (CI posts the summary below)
- [ ] STATUS.md updated (phase, gate: in_review, open questions, next actions)
- [ ] Every new number has a source (datasheet page / URL + date); estimates marked `ESTIMATE:`
- [ ] Decision drafted for docs/decisions.md (owner confirms on merge)
- [ ] Independent review (if this gate has one): `reviews/<phase>/<reviewer>.md` present, level achieved: __ (1 other vendor, 2 other model, 3 other agent/instructions, 4 human); every issue resolved in `resolutions.md`
- [ ] Commits carry `OhmAI-Author:` trailers

### Owner checklist
- [ ] Reviewed the diff, renders and netlist/BOM diffs in the CI comment
- [ ] Decision recorded in docs/decisions.md and STATUS.md → Locked decisions
