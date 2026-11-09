<!-- SPDX-License-Identifier: Apache-2.0 -->
# Phase 1: Requirements

**Goal:** a spec the owner can lock. Branch `phase/requirements`. Check `ohmai check requirements`.

## Inputs
The idea in STATUS.md, plus the owner's answers.

## Do
1. Turn the idea into `docs/requirements.md`. Separate **must** (the board fails without it) from **wish**
   (to trade off later). Give each one an ID, R-01 or W-01.
2. Use SI units with tolerances or limits (e.g. "5 V ±5 %, 3 A continuous"). No adjectives without numbers.
3. Ask for the **target markets** (EU, UK, US, other) every time; it is a required question. Fill the
   target-markets table in `docs/requirements.md` and STATUS.md `markets:`. The `questions` check WARNs
   while `markets:` is empty. Markets drive the phase 3 regulations and marks and the user manual.
4. Cover interfaces, environment (temperature, humidity, IP, indoor/outdoor, lifetime), cost target at a
   stated quantity, size/layer limits, fab/assembly house, and the safety facts (max voltage, touchable parts).
5. Each requirement names its source (owner, standard, datasheet) and how it is verified (test, review,
   analysis).
6. Ask the owner in one batch. Unanswered items stay in Open questions, never as guesses in the table.
   In the table, write "open (Q3)" and keep the question in STATUS.md. Give every question your
   recommendation; research material goes in a section marked PROPOSAL, apart from the must/wish tables.
7. `ohmai check requirements` checks that the file is filled in and WARNs (`questions` check) while
   STATUS.md has open questions not marked "Non-blocking". Even a clean result does not lock the spec;
   only the owner's gate does.

## Outputs
`docs/requirements.md` (template marker removed, no TODO lines) and STATUS.md open questions.

## Gate
The owner reads the must/wish tables and **locks the spec**. Record it as D-001. Later changes need a new
decision.

Owner-facing open questions (STATUS, review OQs, gate summary) follow SKILL.md section 3b: context naming the locked decision not changed, what it is / is not, options with consequences, and no negative product-level wording for subsystem topics.
