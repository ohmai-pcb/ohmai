<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
# Phase 5: Per-part constraints from datasheets

**Goal:** a reviewed `parts/constraints/<PART>.yaml` for every IC and every part that has rules. **This is
the most valuable artifact in the project.** The schematic checks depend on it.
Branch `phase/constraints`. Check `ohmai check constraints`.

## Do (per part)
1. Copy `parts/constraints/TPS63001.example.yaml` as the pattern. The format is in
   `parts/constraints.schema.json`.
2. From the datasheet pin table, write every pin: number, name, **direction** (supply_in, ground,
   regulator_out, switch, gate_drive, bootstrap, input, output, io, open_drain, analog_in/out, feedback,
   sense, passive, nc), abs max, and `cite: {page: N}`.
3. **Never guess a pin direction.** If the table is ambiguous, write `direction: unknown` and add an open
   question. The file cannot be marked reviewed while any pin is unknown.
4. Write the required external parts with min/max/nominal and units, e.g. the VCC cap 0.47-1 µF (p.4) or the
   bootstrap cap 0.22 µF to SW (p.4). Use `to: pin:N` or `to: GND`. Use `total_on_net` when the limit is
   on the sum.
5. Add abs max / recommended ratings, the I2C address (with its page), `evb_reference` for power ICs, and
   layout notes (each with a page).
6. Set `review.status: draft`. Only a human sets `reviewed` (with reviewer and date).

7. For the MCU, cross-check its constraints pin table against `parts/mcu_pin_budget.yaml` (reserved pins,
   ADC-capable pins for `adc` nets). `ohmai check mcu-pin-budget` runs in this phase too and must not FAIL.

## Gate
1. `ohmai review start constraints` → `reviews/constraints/BRIEF.md`. Ask the owner for the highest feasible
   independent reviewer (other vendor > other model > other agent/instructions > human; never yourself) and
   say which level was used.
   If `min_independence` cannot be met, try every better level first and document why each was not available;
   only then ask the owner for a waiver (`accepted_independence`, skill section 3a, docs/REVIEWS.md).
2. The reviewer checks **every line against the datasheet page** and writes `reviews/constraints/<name>.md`.
3. Resolve every issue in `reviews/constraints/resolutions.md`; after fixes, re-run `ohmai review start` and
   get a re-verification. `ohmai check constraints` (incl. `review:constraints`) must pass.
4. The owner approves; then each file gets `review.status: reviewed` with reviewer and date.
5. Once the approval is recorded, continue straight into phase 6 (SKILL.md section 3, step 6). Do not wait for another message.

Owner-facing open questions (STATUS, review OQs, gate summary) follow SKILL.md section 3b: context naming the locked decision not changed, what it is / is not, options with consequences, and no negative product-level wording for subsystem topics.
