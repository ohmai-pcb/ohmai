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

- **2.2.4 fixed-wire combine NPE:** `to_trace_entries is null`,
  `ShapeSearchTree.merge_entries_in_front` / `PolylineTrace.combine_at_start`.
  Fix/protect alone is insufficient; OhmAI `67f5008` coalesces degree-two fixed
  chains without changing geometry. [Crash evidence](https://github.com/ohmai-pcb/ohmai/blob/main/docs/routing/freerouting-log.md#fr-20261109-01--scoped-export-npe).
- **Custom-rule gap:** KiCad `.kicad_dru` conditions/SAFETY envelopes are not fully
  conveyed by DSN. The successful run still adds 10 SAFETY violations and raises
  clearance 55 → 66. [Rejected post-fix run](https://github.com/ohmai-pcb/ohmai/blob/main/docs/routing/freerouting-log.md#fr-20261109-04--coalesced-export-routes-custom-safety-still-ignored).
- **Settings compatibility:** 2.2.4 warns about unknown `Router.*` properties in
  historical logs. Do not assume a requested optimizer/job property was honored;
  retain actual command/output and the external timeout. [Settings evidence](https://github.com/ohmai-pcb/ohmai/blob/main/docs/routing/freerouting-log.md#fr-20261109-01--scoped-export-npe).
- **SES is not acceptance:** exit 0 and nonzero wire/via counts do not establish
  manufacturing compliance or KiCad connectivity. Native staged DRC is the oracle.
