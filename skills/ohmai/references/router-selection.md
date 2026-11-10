<!-- SPDX-License-Identifier: Apache-2.0 -->
# Choose the router before routing

| Use internal handroute | Consider Freerouting only |
|---|---|
| Power/high-current nets, SAFETY/custom rules, keepouts, neck-sensitive connections | Low-risk signal nets in regions without custom-rule dependencies |
| Explicit width floor and legal pad-core contacts matter | An explicitly scoped signal-only DSN is sufficient |

1. Route critical/power nets with internal handroute first. Retain bounded budgets;
   a partial result is not completion. Never lower manufacturing minima.
2. Freerouting is always **staged**: export from a board copy, import SES to a
   different copy, refill, then run full native JSON DRC with `--severity-all`.
   `layout autoroute` is an SES-producing subprocess, not an importer/promoter.
3. Compare before/after unconnected counts on every net, shorts, crossings,
   non-zone clearance, SAFETY and connection_width. Never promote if clearance or
   SAFETY rises, another net worsens, or a new illegal contact/short/crossing appears.
   Missing DRC is **pending**, not permission. No rule waivers.
4. Keep each run's automatic `out/routing/freerouting.jsonl`, Markdown index and
   per-run DSN/SES/tail snapshots. Attach explicit measured before/after JSONs with
   `--drc-before`/`--drc-after` when available; pending sessions still get logged.
5. At project close-out, produce the mandatory **all-tool issue report**, not just
   router notes: see [template](tool-issues.md). Refresh it before fab
   and after bring-up; capture every used tool, including tools with no observed
   issues. Draft upstream titles/bodies; owner approval is required before filing.

## Known limits, with evidence

- **Preferred Freerouting: 2.5.0.** The 2.2.4 `to_trace_entries` /
  `combine_at_start` NPE ([upstream #721](https://github.com/freerouting/freerouting/issues/721))
  is fixed in 2.5.0. Retained crash DSNs complete without OhmAI coalescing.
  [2.5.0 re-test](https://github.com/ohmai-pcb/ohmai/blob/main/docs/routing/freerouting-log.md#fr-20261110--freerouting-250-upgrade-re-test-local).
- **2.2.4 fixed-wire combine NPE (historical):** Fix/protect alone was insufficient;
  OhmAI `67f5008` coalesces degree-two fixed chains without changing geometry.
  Keep `autoroute.dsn.coalesce_fixed_chains` (default true) when a project is still
  on 2.2.4; it is optional on 2.5.0+. [Crash evidence](https://github.com/ohmai-pcb/ohmai/blob/main/docs/routing/freerouting-log.md#fr-20261109-01--scoped-export-npe).
- **Custom-rule gap:** KiCad `.kicad_dru` SAFETY envelopes are still not fully
  conveyed by DSN. On 2.2.4 the coalesced run raised clearance 55 → 66 (+10 SAFETY).
  On 2.5.0 the same scoped nets on `6bb6f50` held clearance/SAFETY flat when only
  F.Cu/B.Cu SES wires were imported — still **not** a promotion approval.
  [Rejected 2.2.4 run](https://github.com/ohmai-pcb/ohmai/blob/main/docs/routing/freerouting-log.md#fr-20261109-04--coalesced-export-routes-custom-safety-still-ignored);
  [2.5.0 comparison](https://github.com/ohmai-pcb/ohmai/blob/main/docs/routing/freerouting-log.md#board-safety-comparison-6bb6f50-scoped-nine-nets).
- **Settings:** Prefer nested `--router.autorouter.*` / `--router.optimizer.*` /
  `--router.job_timeout` / `--router.scoring.via_costs` (2.5.0). Legacy flat
  `Router.*` keys warn as deprecated. Retain actual command/output and the
  external timeout.
- **Native CLI caveat:** Freerouting 2.5.0 `freerouting-cli` (GraalVM) hit an
  unsupported `ComponentOutline` serialization error on these DSNs; use the
  **jar + JDK 25** path OhmAI already drives until that is confirmed fixed.
- **SES is not acceptance:** exit 0 and nonzero wire/via counts do not establish
  manufacturing compliance or KiCad connectivity. Native staged DRC is the oracle.
  Skip or separately review plane-layer (`GND`/`PWR`) SES wires from 2.5.0.
