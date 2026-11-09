# Freerouting evidence log

One entry per invocation. Never infer DRC success from an SES file or Java exit 0.
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

Common board: usb-c-12v-stereo, copper `a70513b` (docs-only `3a8a459`). Scope:
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
- DSN: `$OHMAI_ROUTING_EVIDENCE/validated-autoroute/usb-c-12v-stereo.dsn`;
  options/run counters: `validated-autoroute/report.json`; measurements:
  `validated-autoroute/drc-refilled.json` and refilled candidate PCB.
- Minimal-repro note: compare the patched DSN's allowed routes against native
  KiCad custom-rule envelopes around fuse/spade pads. Retain imported/refilled
  PCB and JSON DRC as the oracle; no standalone minimal custom-rule board yet.
  Upstream candidate: **yes, rule-format/model gap** (not necessarily an
  upstream bug: DSN cannot express all KiCad rules).

See [router selection](router-selection.md). Preserve unknown measurements as null
in machine logs. Do not file or upload upstream issues without owner approval.
