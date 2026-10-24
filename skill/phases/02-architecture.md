# Phase 2: Architecture and trade study

**Goal:** the owner picks one architecture. Branch `phase/architecture`. Check `ohmai check architecture`.

## Do
1. Propose 2-3 real options (topologies or major ICs) that meet every must. Drop any option that misses a must.
2. Draw a **block diagram** (mermaid) in `docs/architecture.md`.
3. Fill the **comparison table** against the requirements: efficiency, cost (ESTIMATE, with how you got it),
   part count and area, risk, sourcing.
4. Write the **power tree and budget**: every rail, source, voltage range, typical/max current, losses.
   Sum the losses.
5. Write the **bus map**: bus, devices, addresses, speed, where the pull-ups are, voltage domains. Flag
   address clashes now.
6. Do a **thermal** first pass: losses per hot part, θJA from the datasheet, Tj at max ambient, margin.
7. Plan the **sheets**: one functional block per schematic sheet.
8. Recommend one option and give the reasons.
9. Every **limitation the user must know** (e.g. a probe type that is not supported, a common-mode
   limit, a temperature or supply limit) gets a manual-warning ID: add `- MW-xx: ...` under STATUS.md
   "Manual warnings" and name the ID in `docs/architecture.md`. It flows into the safety plan (phase 3)
   and the manual's safety section (`docs/manual.md`). The `manual-warnings` check enforces the link.

## Gate
The owner picks an option. Record it in decisions.md together with the options that were rejected.
