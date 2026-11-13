<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
# Freerouting evidence log

One entry per invocation. Never infer DRC success from an SES file or Java exit 0.

This ohmai release pins Freerouting **2.5.0** (jar + JDK 25) in `pinned-tools.yaml`.
Historical entries below that used 2.2.4 remain as evidence; new runs should use the
pinned jar (`ohmai setup`, `FREEROUTING_JAR` / the ohmai cache path).
`ohmai layout autoroute` automatically appends project-local JSONL and a Markdown
line under `out/routing/`, and snapshots DSN/SES and captured router tails into a
unique run directory. Failures and prepare-only calls are included. Optional
`--drc-before` / `--drc-after` JSON reports attach measured staged-import/refill
results; otherwise DRC is explicitly **pending**, never zero. The command itself
does not import or promote copper. No logging opt-out. Missing-project discovery
cannot write a project log; logging failures return nonzero.

Historical evidence bundle: `OHMAI_ROUTING_EVIDENCE` denotes the retained local
`ohmai-routing-20261109` bundle (not distributed in this repository). Paths below
are relative to that bundle. Keep it with an upstream issue's attachments. Unknown
historical fields remain unknown rather than guessed. All dates are 2026-11-09.
The three crash entries are distinct retained diagnostic invocations, not a claim
that only three crashes occurred during reduction experiments.

Common board (anonymized label): reference-power-board, copper `a70513b` (docs-only `3a8a459`). Scope:
`3V3_LOGIC,STEREO_12V,V_BATT_PROT,STEREO_SRC,CHASSIS,VDDA,BATT_P,ACC_OUT,VBUS_20V`;
exclude `GND,SW2_NODE,VSYS_BB`. Freerouting 2.2.4. Baseline KiCad unconnected: 258;
non-zone clearance: 55. Crashes were not imported, so after-DRC and emitted new
wire/via counts are **not measured**. The post-fix run used JDK 25; exact JDK
builds for the three earlier diagnostic invocations were not retained.

## FR-20261109-01 — scoped export NPE

- Date: 2026-11-09. Board/commit and scoped nets: common case above.
- Freerouting: 2.2.4; JDK: historical build unrecorded.
- Options: scoped patched export; exact pass/thread/timeout command not retained.
  Output warns that `Router.max_passes`, `Router.optimizer_max_passes` and
  `Router.job_timeout` are unknown settings; do not assume these properties worked.
- Outcome: routing-pass failure; wires/vias and after DRC unknown; no promotion.
- Crash: `NullPointerException: Cannot load from object array because "to_trace_entries" is null`
  in `ShapeSearchTree.merge_entries_in_front` → `PolylineTrace.combine_at_start`.
- Rule gap: custom KiCad SAFETY rules are not fully represented in DSN; not
  separately measured because this invocation crashed.
- DSN: `$OHMAI_ROUTING_EVIDENCE/baseline.dsn`; stack/output: `baseline/stderr.txt`,
  `baseline/stdout.txt`.
- Minimal-repro note: retained board is not a standalone minimal reduction. Isolate
  fixed, degree-two VBUS_20V wire chains; coalescing three wire records to one
  vertex-preserving polyline stops the search-tree failure. No duplicate or
  zero-length segments were found. Upstream candidate: **yes, crash**.

## FR-20261109-02 — fixed-wire diagnostic still NPE

- Date: 2026-11-09. Same board/commit/scope; Freerouting 2.2.4, JDK build unrecorded.
- Options: mark imported wire records fixed; other exact options not retained.
- Outcome: same `to_trace_entries is null` / `merge_entries_in_front` /
  `combine_at_start` stack. New wires/vias and after-DRC unknown; no promotion.
- Rule gap: custom SAFETY not fully exported; not measured after this crash.
- DSN: `$OHMAI_ROUTING_EVIDENCE/all-fixed.dsn`; `all-fixed/stderr.txt` and
  `all-fixed/stdout.txt`.
- Minimal-repro note: changing fixed state alone does not prevent the failure;
  preserve geometry and compare degree-two chain coalescing. Upstream: **yes**.

## FR-20261109-03 — protected-wire diagnostic still NPE

- Date: 2026-11-09. Same board/commit/scope; Freerouting 2.2.4, JDK build unrecorded.
- Options: protect imported copper; other exact options not retained.
- Outcome: same NPE signature; new wires/vias and after-DRC unknown; no promotion.
- Rule gap: custom SAFETY not fully exported; not measured after this crash.
- DSN: `$OHMAI_ROUTING_EVIDENCE/protected.dsn`; `protected/stderr.txt` and
  `protected/stdout.txt`.
- Minimal-repro note: protect alone fails, while geometry-preserving chain
  coalescing succeeds. Upstream: **yes, crash**.

## FR-20261109-04 — coalesced export routes, custom SAFETY still ignored

- Date: 2026-11-09. Same board/commit/scope. OhmAI fix `67f5008`.
- Freerouting: 2.2.4; JDK: 25 (exact build unrecorded).
- Options: 3 passes, optimizer 0, 60-second process timeout, via cost 50,
  one thread; 125 fixed-wire chain joins, preserving net/layer/width/vertices.
- Outcome: **113 new SES wires / 20 vias**, 218 PCB segments after import;
  unconnected **258 → 205**; shorts **0 → 0**, crossings **0 → 0**;
  non-zone clearance **55 → 66 (+11)**, including **10 new SAFETY violations**.
  Crash: none. **Rejected for promotion**, not a clean board.
- Ignored rule: custom `.kicad_dru` SAFETY footprint/spade/fuse clearances (2.00 mm).
- DSN: `$OHMAI_ROUTING_EVIDENCE/validated-autoroute/reference-power-board.dsn`;
  options/run counters: `validated-autoroute/report.json`; measurements:
  `validated-autoroute/drc-refilled.json` and refilled candidate PCB.
- Minimal-repro note: compare the patched DSN's allowed routes against native
  KiCad custom-rule envelopes around fuse/spade pads. Retain imported/refilled
  PCB and JSON DRC as the oracle; no standalone minimal custom-rule board yet.
  Upstream candidate: **yes, rule-format/model gap** (not necessarily an
  upstream bug: DSN cannot express all KiCad rules).

See [router selection](router-selection.md). Preserve unknown measurements as null
in machine logs. Do not file or upload upstream issues without owner approval.

## Other retained reduction/control invocations (same date)

These are separate runs, not extra placement attempts recommended by the procedure.
They use the same stereo `a70513b` source; isolated DSN edits are indicated below.
For each: exact JDK build and complete command/options were not retained; native
before/after DRC deltas are **unmeasured** (source baseline 258, clearance 55);
no candidate was promoted. `wires/vias` here means parsed non-fix/protect **SES
records**, not verified new physical progress. Custom SAFETY export remains a
known gap but was not measured in these controls. All paths are evidence-bundle
relative. Each DSN still needs a standalone minimal reduction before upstream filing.

| Run ID | Freerouting | Scoped/diagnostic input DSN | SES records wires/vias | Outcome / crash / upstream candidate | Repro output / minimal-repro note |
|---|---|---|---:|---|---|
| FR-20261109-05 | 2.2.4 | `excluded-keepout.dsn`; common nine-net scope | 1/0 | Same `to_trace_entries` / merge/combine NPE; upstream yes | `excluded-keepout-2.2.4/{stdout,stderr}.txt`; excluded-copper keepout control does not remove crash |
| FR-20261109-06 | 2.2.4 | `pruned.dsn`; common scope | 1/0 | Same NPE; upstream yes | `pruned/{stdout,stderr}.txt`; preserve this pruned control while reducing fixed wire chains |
| FR-20261109-07 | 2.2.4 | `no-wire-diagnostic.dsn`; wire-removal isolation control | 111/19 | No NPE seen, session completed; rule-gap candidate only, not accepted | `no-wire-diagnostic/{stdout,stderr}.txt`; destructive diagnostic, not a geometry-preserving workaround |
| FR-20261109-08 | 2.2.4 | `wire-only.dsn`; wire/via isolation control | 1/0 | Same NPE; upstream yes | `wire-only/{stdout,stderr}.txt`; retained wires alone still trigger failure |
| FR-20261109-09 | 2.2.4 | `via-only.dsn`; complementary isolation control | 113/19 | No NPE seen, session completed; rule-gap candidate only | `via-only/{stdout,stderr}.txt`; removing fixed wires changes behavior, not a valid final-board repair |
| FR-20261109-10 | 2.2.4 | `coalesced.dsn`; fixed-chain coalescing control | 103/17 | No NPE seen, session completed; rule-gap candidate only | `coalesced/{stdout,stderr}.txt`; reduce degree-two chain conversion while retaining every vertex |
| FR-20261109-11 | 2.2.4 | `canonical-all.dsn`; canonicalized fixed-wire control | 113/20 | No NPE seen, session completed; rule-gap candidate only | `canonical-all/{stdout,stderr}.txt`; compare geometry-preserving canonicalization against failing raw chains |
| FR-20261109-12 | 2.1.0 | Input path/scope not retained | unavailable | No retained SES or completed-session evidence; no NPE seen; analytics SSL `CompletionException` appears while routing continues; not a proven fix | `fr21/run.log`; last retained output reaches pass 30; command/input/reduction must be recovered before upstream filing |


## FR-20261110 — Freerouting 2.5.0 upgrade re-test (local)

Evidence bundle: `$OHMAI_FR25_EVIDENCE` (retained local bundle, not distributed). Board: reference-power-board
copper `6bb6f50`. Scope same as FR-20261109-01..04. Freerouting **2.5.0** jar +
OpenJDK 25.0.4.1; private `--user_data_path` so the shared 2.2.4 config was untouched.
Additive SES import kept F.Cu/B.Cu only (2.5.0 also emits plane-layer `GND`/`PWR`
wires; those were skipped for KiCad legality). Native CLI (`freerouting-cli`) hung
on a GraalVM `ComponentOutline` serialization error and was **not** used for
measurements.

### Upstream NPE status

FR-20261109-01..03, 05, 06, 08 (`to_trace_entries` / `combine_at_start`) are
**fixed upstream** by [freerouting#721](https://github.com/freerouting/freerouting/issues/721)
and present in 2.5.0. Repro DSNs that crashed on 2.2.4 (`raw`, `baseline`,
`all-fixed`, `protected`, `pruned`, `wire-only`, `excluded-keepout`) completed on
2.5.0 with **no NPE**, with or without OhmAI fixed-chain coalescing.

| Run ID | Freerouting | Input | SES wires/vias (tokens) | NPE | Notes |
|---|---|---|---:|---|---|
| FR-20261110-01 | 2.5.0 | `raw.dsn` (prior evidence) | 508/149 | no | Completes; fanout+autoroute |
| FR-20261110-02 | 2.5.0 | `baseline.dsn` | 138/25 | no | Was crash on 2.2.4 |
| FR-20261110-03 | 2.5.0 | `all-fixed.dsn` | 138/24 | no | Was crash on 2.2.4 |
| FR-20261110-04 | 2.5.0 | `protected.dsn` | 150/30 | no | Was crash on 2.2.4 |
| FR-20261110-05 | 2.5.0 | `pruned.dsn` | 137/24 | no | Was crash on 2.2.4 |
| FR-20261110-06 | 2.5.0 | `wire-only.dsn` | 130/24 | no | Was crash on 2.2.4 |
| FR-20261110-07 | 2.5.0 | `excluded-keepout.dsn` | 137/24 | no | Was crash on 2.2.4 |
| FR-20261110-08 | 2.5.0 | `coalesced.dsn` | 137/24 | no | Control; still completes |
| FR-20261110-09 | 2.5.0 | `canonical-all.dsn` | 137/24 | no | Control; still completes |

### Board SAFETY comparison (6bb6f50, scoped nine nets)

Same scope/exclusions as FR-20261109-04. Metrics via `native_guard.metrics` after
zone refill. 2.2.4 numbers from FR-20261109-04 (a70513b copper, coalesced).

| Version | Coalesce | SES route wires/vias | nonzone clearance | SAFETY | shorts/crossings | unconnected |
|---|---|---:|---:|---:|---|---:|
| 2.2.4 (FR-20261109-04) | yes (125 joins) | 113/20 | 55 → **66** | +10 new | 0/0 | 258 → 205 |
| 2.5.0 FR-20261110-10 | yes (478 joins) | 157/17 | 55 → **55** | 9 → **9** | 0/0 | 192 → 185 |
| 2.5.0 FR-20261110-11 | no | 159/17 | 55 → **55** | 9 → **9** | 0/0 | 192 → 185 |

**Qualification (2026-11-10):** both 2.5.0 imports discarded **27 GND-layer and
31 PWR-layer wire records**. The 55/9 clearance/SAFETY and 192→185 measurements
are valid only for that incomplete F.Cu/B.Cu subset, not the full routed solution.
The layers were incorrectly available because OhmAI stripped plane polygons and
failed to emit default class `use_layer` constraints. These numbers do **not**
show that 2.5.0 fixed custom-rule compliance, and the 2.2.4 comparison also used a
different copper baseline. Withdraw any general SAFETY improvement inference.
The no-NPE observations above remain independent evidence about issue #721.

The incomplete subset did not regress the measured counters. Coalescing is **optional** on 2.5.0+ (`autoroute.dsn.coalesce_fixed_chains`,
default still true for 2.2.4 compatibility). Custom KiCad SAFETY rules remain a
DSN modelling gap — do not treat SES success as promotion. The plane-layer routing preparation bug must be fixed before a full-SES trial;
keep the F/B orphan-via filter and native guard, not the invalid plane stubs.

## FR-20261113 — derived signal layers + plane reservations (local)

Board: reference-power-board copper `188d7e0` (4 layers; inner GND/PWR power-typed;
`planes: {GND: In1.Cu}` only). Scoped 2.5.0 run on the 31 nets of the earlier stubbed
pass, 30 passes, 0 optimizer passes, 8 min job timeout, zero extra DSN config.
DSN log: dedicated-plane areas kept for GND/PWR, DSN-only reservation on empty PWR,
every class `use_layer F.Cu B.Cu`.

| Check | Before fix (same scope) | After fix |
|---|---|---|
| SES route wires on GND/PWR | 55 of 131 | **0** of 135 (77 F.Cu, 58 B.Cu) |
| SES vias kept by the F/B one-layer filter | 0 of 33 | **25 of 27** (2 dangling FR stubs dropped) |
| Kept vias with same-net copper on F.Cu **and** B.Cu | 0 | **25 of 25** |

Per-net additive import, fresh-process refill native guard after each net
(reject on any hard gate, SAFETY > 9, nonzone > 55, any net worse, SW2_NODE/VSYS_BB
unconnected): 7 nets accepted (Q3-G, Q5-G, Q4-G, U7-OUT 1→0; D_CC2-A1 2→0;
I2C_SDA 3→2; U4-PA8 2→1). Cumulative gate: unconnected 49 → 41, shorts/crossings/
hole clearance/connection width 0, SAFETY 9 → 9, nonzone 55 → 55, no net worse,
SW2_NODE/VSYS_BB 0 unconnected. Rejected: ADC_ISNS, J5-Pin_3, J5-Pin_4 (SAFETY 13–17),
D_ESD2-A1 (nonzone 56), U1-CELL_BATPRESZ (splits VSYS_BB), STEREO_PGD and U5-OVP
(split V_BATT_PROT 1→2), JP1-Pin_1 and Q7A-G (no target improvement). Shared-layer
pour splitting and custom SAFETY rules remain DSN modelling gaps; keep the guard.
