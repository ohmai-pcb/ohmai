---
project: "{{project_name}}"
ohmai_version: "{{ohmai_version}}"
phase: 1            # 1-12, see .ohmai/phases/
gate: open          # open | in_review | approved
markets: []         # target markets, e.g. [EU, UK, US]; owner answers in phase 1
authors:            # identities that write this project's work; reviewers must differ (ohmai check review)
  - {name: "", kind: agent, vendor: "", model: "", instructions: "ohmai skill {{ohmai_version}}"}
updated: {{date}}
---
<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->

# STATUS: {{project_name}}

> This file is the spec of record. If chat and this file disagree, this file wins until the owner changes it.
> Agents update it in the same commit as the work. Full decision entries live in `docs/decisions.md`.

## Idea
{{idea}}

## Current phase
Phase 1: Requirements. Branch `phase/requirements`. Gate: owner locks the spec (`docs/requirements.md`).

## Locked decisions
| ID | Date | Decision | Phase | Ref |
|----|------|----------|-------|-----|

## Open questions
- [ ] Who is the owner (the person who approves gates)?
- [ ] Target quantity and cost target per board?
- [ ] Target markets (EU, UK, US, other)? They set the regulations, marks and user-manual rules (phase 3, phase 12). Record them in `markets:` above.

## Manual warnings
<!-- One line per warning the user manual must carry: "- MW-01: <hazard / limitation>, source (phase, doc)". docs/manual.md references every ID. -->

## Next actions
- Agent: draft `docs/requirements.md` from the idea; list must-haves vs wishes; ask the owner the open questions.

## Phase log
| Phase | Started | Gate passed | PR |
|-------|---------|-------------|----|
| 1 Requirements | {{date}} | | |
