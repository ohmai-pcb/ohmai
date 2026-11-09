<!-- SPDX-License-Identifier: Apache-2.0 -->
# Phase 10: BOM lock

**Goal:** the fab buys exactly what was approved. Branch `phase/bom`. Check `ohmai check bom`.

## Do
1. Set every fitted row in `bom/bom.csv` to `status: locked` with an MPN. Only listed `approved_alternates`
   may be substituted. Verify `ohmai check bom-lock` passes all 3 BOM sourcing rules (generic >=2 concrete
   alternates from unique makers, non-generic >=2 unique manufacturers, and single-source parts qualified with
   `D-xxx` + `checked:` exhausted candidate search and listed in the `SINGLE-SOURCE COMPONENTS` highlight block).
2. Recheck stock, lifecycle and price (with source and date) for every row.
3. Get the fab's quote and its **actual-purchase list**: the MPNs it will really buy, including the
   passives it picks itself. Save it as `bom/fab-actual.csv`.
4. Run `ohmai check bom-diff` (or `--fab <file>`). Every difference must be in `approved_alternates`, or be
   approved by the owner and added to the BOM first. Check the ratings, not just the values.

> Real lesson: PCBWay substituted MP2651-P with MP2651-Z and chose its own MPNs for the passives. One
> resistor was 0.2 W where the design needed 250 mW. Only diffing the actual-purchase column caught it.

## Gate
The owner approves the diffed actual-purchase list. Then, and only then, the order can be placed.

## Independent review
Before the gate: `ohmai review start bom`, get an independent review at the highest feasible level (skill
section 3a), resolve every finding in `reviews/bom/resolutions.md`; `ohmai check review:bom` must pass.

If this gate's `min_independence` cannot be met, try every better level first and document why each was not
available; only then ask the owner for a waiver (`accepted_independence`, skill section 3a, docs/REVIEWS.md).
