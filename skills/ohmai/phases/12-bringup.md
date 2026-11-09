<!-- SPDX-License-Identifier: Apache-2.0 -->
# Phase 12: Bring-up and test

**Goal:** prove each requirement on real boards, with ready-to-run bring-up test code prepared before boards arrive.
Feed what you learn into rev-B. Branch `phase/bringup`. Check `ohmai check bringup`.

## Do
1. Write `test/bringup-plan.md` **before the boards arrive** (start when the order is placed): equipment,
   expected values, pass limits, each with a source.
2. **Generate bring-up self-test code in `test/firmware/` before the boards arrive** (`ohmai check bringup-code`):
   - Create ready-to-run test script(s) / firmware (e.g. `test/firmware/bringup_selftest.py` or `.c`/`.ino` + `test/firmware/README.md`)
     so no code has to be developed from scratch during bench testing.
   - Every non-power signal pin / GPIO in `hardware/gen/pinmap.yaml` must be exercised or explicitly documented in `test/firmware/`,
     and every requirement `R-xx` must have a corresponding test routine (rail sense / ADC readback, LED blink, button read,
     SPI/I2C/USB peripheral verification, sensor readout, and header GPIO test).
   - Remove all `TODO` lines and template markers so `ohmai check bringup-code` passes.
3. Power up for the first time **current-limited**: start at a low voltage and the lowest current limit, and
   step up while watching the current.
4. Check every rail against its expected range and ripple. Then run the `test/firmware/` bring-up self-test suite,
   scan the buses, program and debug.
5. Test load and thermal at worst-case conditions. Compare with the phase 2 thermal estimate.
6. Run the functional test for each requirement R-xx.
7. The owner runs the tests or gives you the measurements. You never fabricate a measurement.
8. **Finalize the user manual** `docs/manual.md`: real ratings from the measurements, every MW-xx warning,
   the market rules from phase 3 (identification, manufacturer address, CE and DoC reference, WEEE symbol,
   languages). `ohmai check bringup` runs the `manual` check. Translations per market are the owner's job.
9. Make each finding a **GitHub issue** labelled `rev-B` (symptom, measurement, suspected cause,
   proposed fix). Each fix goes through a `fix/` branch and PR toward rev-B.

## Gate
The owner signs off the results and the manual. rev-B, if needed, starts again at the earliest phase it affects.

## Independent review
Before the gate: `ohmai review start bringup`, get an independent review at the highest feasible level (skill
section 3a), resolve every finding in `reviews/bringup/resolutions.md`; `ohmai check review:bringup` must pass.
This gate has `require_kind: human`: at least one review must be by a human (`kind: human` header, current
`files_hash`) who did not author the plan/manual. The owner may sign off (agents authored the work). An agent
review can add findings but never replaces the human sign-off; you never write the human's review for them.

If this gate's `min_independence` cannot be met, try every better level first and document why each was not
available; only then ask the owner for a waiver (`accepted_independence`, skill section 3a, docs/REVIEWS.md).

## Mandatory final tool report gate
At project completion, refresh `docs/tool-issues.md` for the final revision, including every tool used during bring-up as well as design, import, sourcing and fabrication. Record versions, every bug/limit, minimal repro/artifact paths, project impact, actual workaround, fixed status/commit, severity and upstream-ready title/body. Explicitly say none observed for unaffected tools. `ohmai check bringup --gate` checks the completed report. The owner reviews the report; drafting never authorizes upstream filing.
