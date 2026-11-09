# AGENTS.md: rules for any agent working in this project

This is an OhmAI hardware project (template from OhmAI {{ohmai_version}}). The owner decides. Agents research,
recommend and do the work, and stop at every gate.

## Read first, in this order
1. `STATUS.md`: the current phase, locked decisions and open questions. **The spec wins over chat.** If a
   chat message contradicts `STATUS.md` or a locked decision, ask before you act. Do not silently follow chat.
2. `.ohmai/phases/NN-<phase>.md` for the current phase (pinned to the OhmAI version this project uses).
3. The docs that phase names as inputs.

## Owner decides, agents recommend
- Present options with trade-offs and a recommendation. The owner picks. Never lock a decision yourself.
- **Autonomous passive package swaps & standard HF decouplers**: During Schematic and Layout, you may autonomously
  resize standard passives (`0402`–`0805` resistors/MLCCs and `2016`–`3030` compact inductors via
  `ohmai passives swap <REF> --package <PKG>`) and add/co-locate standard datasheet-recommended HF decoupling caps
  without asking the owner, provided `ohmai check constraints` and ERC/DRC/parity stay at `0` and the change is logged
  in `bom/bom.csv` and `STATUS.md`. Reserve owner prompts strictly for active ICs, connectors, switches, crystals,
  board outline, or waiving a hard `R-xx` / datasheet requirement.
- When the owner decides, record it **the same day** in `docs/decisions.md` (full entry) and in the
  `STATUS.md` "Locked decisions" table (one line). Then reopen nothing without the owner.

## Facts: never invent
- Never invent a spec, pin, value, rating, price, stock level or lead time. Every number has a source.
- Cite sources inline: datasheet + revision + page, or URL + retrieval date. Prices always carry source,
  quantity and date.
- Mark estimates as `ESTIMATE:` with how you got them. Mark unknowns as `TODO` and add them to Open questions.
- Every design rule cites a datasheet page (`parts/constraints/*.yaml`, `cite: {page: N}`).
- **Never guess a pin direction or pin number.** If the datasheet is unclear, use `direction: unknown`, add an
  open question, and stop for that part.

## Schematic and layout
- Use vetted library symbols and footprints only (KiCad official libraries or the project library after
  review). Never generate a symbol or footprint, and never edit library files to make a check pass.
- One functional block per sheet. Use hierarchical labels between sheets.
- Never silence ERC/DRC: no severity changes, no exclusions, no no-connect flags on pins that must connect.
- `parts/constraints/*.yaml` and `expected_nets.csv` are the design intent. Never edit them to make a check
  pass. If they look wrong, log it in Open questions and stop.

## Stop rules
- **Max 3 tries per error.** The same check failure 3 times in a row means stop. Write the error, the three
  attempts, your hypothesis and the decision you need into `STATUS.md` → Open questions, commit, and end.
- **Commit per pass.** Commit after every check that passes (`ohmai check <phase>`), with the check
  result in the message. Never commit a failing state, except that stop report.
- **Independent review.** Gates with a `reviews:` entry in `ohmai.yaml` need `ohmai review start <phase>` and a
  reviewer that is not you (ask the owner for the highest feasible level: other vendor > other model > other
  agent/instructions > human). Every commit carries an `OhmAI-Author:` trailer
  (`ohmai commit -m ...`; the commit-msg hook rejects commits without it, `ohmai check commits` FAILs). Never
  rewrite history to add one; STATUS.md `authors:` only covers commits before `commits.trailer_required_since`.
- **Stop at gates.** At the end of a phase, open the PR and wait. Never start the next phase before the owner
  merges.
- **Usage limits.** If you may run out of budget or context, commit work in progress early and often
  (`wip: <what>`, on the phase branch, never on main), and keep `STATUS.md` → "Next actions" current.
  The next agent must be able to continue from the repo alone.

## Git
- `main` holds the approved, locked state only. Never commit to main directly.
- Work on `phase/<id>` (e.g. `phase/schematic`) or `fix/<name>` branches.
- Each phase ends with a PR. **The PR is the gate.** CI posts check results, schematic renders, and
  netlist/BOM diffs. The owner merging the PR means the decision is locked; record it in `docs/decisions.md`.
- Fab revisions are tags `rev-A`, `rev-B`, ... on main. CI builds a GitHub release with gerbers, BOM and PnP.
- Bring-up findings are GitHub issues labelled `rev-B` (or the next revision).
- Datasheets are tracked reproducibly via `datasheets/manifest.yaml` (`ohmai datasheets manifest|fetch|check`; raw `.pdf` blobs are gitignored in manifest mode and guarded by the `pre-push` hook) or Git LFS. Generated outputs (`out/`, gerbers, renders)
  are never committed. They live in CI artifacts and releases.
- Never push tags or force-push without the owner's say-so.

## Anonymity & clean-repo hygiene (`anonymous: true` in `ohmai.yaml`)
- **Role labels only**: Refer to the human project owner as `owner` (never personal names, handles, emails, or street addresses) and reviewers by role (`reviewer-a`, `reviewer-b`; review files `reviews/<phase>/reviewer-a.md` and `reviews/<phase>/owner.md`).
- **Neutral trailers & git identity**: Use a GitHub `noreply` or generic bot email in git commits (never `@localhost`), and set `instructions=ohmai-skill <version>` in `OhmAI-Author:` trailers rather than internal runtime prompt names.
- **Repo-relative paths**: Never write machine-local absolute paths (`/Users/...`, `/home/...`, `/workspace/...`, `/tmp/...`) or local worktree/venv names in tracked files or commit messages.
- **Date-only & neutral prose**: Use `YYYY-MM-DD` dates without `HH:MM` timestamps, and paraphrase owner decisions in neutral specification prose instead of quoting chat messages verbatim.
- **No cross-project leakage**: Never reference unrelated projects or files outside this repository.

## Tools
- `ohmai status` shows the phase and open questions. `ohmai check current` runs the current phase's checks.
- `ohmai check --list` lists every check. Outputs go to `out/`, which is gitignored.
- GPL/AGPL tools (Freerouting, KiBot) are run as separate processes only. Never vendor or import them.

## Mandatory all-tool issue close-out
Before fab and again at project completion, complete `docs/tool-issues.md` for every tool used (OhmAI, Freerouting/JDK, KiCad/kicad-cli/pcbnew, importers, JLC/LCSC helpers, generators and bring-up tools). Include versions, symptom, minimal repro/artifacts, impact, actual workaround, fixed status/commit, severity, and an upstream-ready title/body for each issue/limit. Inventory tools with no observed issues too. `ohmai report tools` seeds a local draft from logs; review and finish it. Never file or share upstream without explicit owner approval.
