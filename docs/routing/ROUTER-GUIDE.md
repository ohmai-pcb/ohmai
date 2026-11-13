<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
# Router guide

When to use OhmAI handroute versus Freerouting, how to run each, how to scope
a run, and which gates must pass before copper is kept. This page is the
how-to. Policy and known limits with evidence live in
[router-selection.md](router-selection.md); historical run evidence lives in
[freerouting-log.md](freerouting-log.md). Per-run logging format:
[FREEROUTING-RUNS.md](FREEROUTING-RUNS.md). Layout CLI details:
[LAYOUT.md](../LAYOUT.md).

## Which router

Use **`ohmai layout handroute`** for power and high-current nets, nets with
SAFETY or other custom `.kicad_dru` rules, keepouts, and neck-sensitive
connections, or whenever an explicit width floor and legal pad-core contacts
matter.

Use **`ohmai layout autoroute`** (Freerouting 2.5.0, staged) only for
low-risk signal nets in regions that do not depend on custom rules, and only
when an explicitly scoped signal-only DSN is enough. Route critical and power
nets with handroute first. Never lower manufacturing minima.

Freerouting is always staged: export from a board copy, import the SES to a
different copy, refill, then run full native JSON DRC with `--severity-all`.
`layout autoroute` prepares a DSN and runs Freerouting as a subprocess; it
does not import or promote copper. An SES file and a Java exit 0 are not a
DRC pass.

## How to run handroute

```sh
ohmai layout handroute route --nets NET1,NET2 --dry-run --json --budget-seconds 60
ohmai layout handroute route --nets NET1,NET2 --staged --budget-seconds 60
ohmai layout handroute apply
```

`--nets` is a comma-separated list of exact net names. `--staged` writes a
copy; `--dry-run` runs the same native checks but writes no copper. Time,
expansion and search caps are shared across every net and width attempt (see
[LAYOUT.md](../LAYOUT.md#bounded-handroute)). A path must join at least two
terminal islands to count as progress. Budget exhaustion returns nonzero and
any generated copper is partial, not a completed route.

Island counts (`initial_islands` / `predicted_islands`) are meant to match
KiCad connectivity on the stored fill, including custom pads and inner-layer
pours. Refill first if the pours are stale. Do not treat a dry-run prediction
of one island as proof that the input board is already closed.

## How to run Freerouting

This release pins Freerouting **2.5.0** (the jar, with JDK 25) in
`pinned-tools.yaml`. `ohmai setup` fetches that jar and verifies its sha256;
`ohmai doctor` FAILs on a different version unless `OHMAI_ALLOW_UNPINNED=1`.
Use the jar with JDK 25, not the GraalVM `freerouting-cli`. The Java binary
is taken from `FREEROUTING_JAVA` if set, otherwise a discovered JDK 25.
`ohmai layout autoroute` always passes `--user_data_path` under
`~/.local/share/ohmai/freerouting/<ver>/userdata` so the shared
`~/.config/freerouting` is never touched.

```sh
ohmai layout autoroute --nets 'SIG_.*' --exclude-nets 'GND|VBUS_.*' \
    --opt-passes 2 --timeout 00:10:00 --json
ohmai layout autoroute --prepare-only --json   # DSN and config only
```

`--nets` and `--exclude-nets` are regular expressions against net names.
`--nets` keeps matching nets in the DSN network; `--exclude-nets` drops
matches. Prefer nested `--router.autorouter.*` / `--router.optimizer.*`
settings (2.5.0); legacy flat `Router.*` keys warn as deprecated.

Every invocation appends `out/routing/freerouting.jsonl`, a Markdown index
line, and DSN/SES/tail snapshots in a unique run directory. There is no
logging opt-out. Attach measured before/after DRC with `--drc-before` /
`--drc-after` when available; otherwise DRC is **pending**, never zero.

## Gates before keeping copper

After refill, compare before and after on every net. Do not promote a run if
clearance or SAFETY rises, another net worsens, or a new illegal
contact, short or crossing appears. Missing DRC is pending, not permission.
No rule waivers.

Hard gates used with Freerouting and handroute:

- shorting, crossings, hole_clearance and connection width at **0**
- island / unconnected counts matching KiCad (no net worse than the baseline)
- SAFETY and non-zone clearance must not increase; project boards may also
  set absolute caps (see [router-selection.md](router-selection.md))

Handroute’s native gate also refuses new sub-floor contacts and an increased
KiCad unconnected count on any net, including non-target nets. Full before
and after DRC JSON is kept under `work_dir/handroute-verification/`.

## Known Freerouting 2.5.0 behaviour

Facts from this repository (CHANGELOG, LAYOUT.md, git history, and the
evidence log). Do not treat this list as a complete upstream bug catalogue.

- **NPE DSN fix.** Freerouting 2.2.4 could crash in
  `to_trace_entries` / `combine_at_start` (upstream
  [freerouting#721](https://github.com/freerouting/freerouting/issues/721)).
  That NPE is fixed in 2.5.0. OhmAI can still coalesce degree-two fixed
  chains (`autoroute.dsn.coalesce_fixed_chains`, default true for 2.2.4
  compatibility); it is optional on 2.5.0+.
- **Plane-layer fix.** With zero optional configuration, `layout autoroute`
  derives signal layers from the board stackup and `planes`. Every class
  gets `use_layer` with signal layers only (typically `F.Cu B.Cu`).
  Dedicated inner planes are kept; an empty power layer gets a DSN-only
  pinless plane reservation so Freerouting does not treat it as a signal
  layer. Through-vias still work. Explicit `signal_layers` /
  `inner_signal_layer` still override.
- **`non_signal_layer_wires`.** A run whose SES has wires on a non-signal
  layer fails and records layer counts. Do not import those stubs.
- **`FREEROUTING_JAVA`.** Overrides the Java binary; a stale `java` token
  baked into a prepared command is replaced. The GraalVM native CLI is not
  the supported path (it has hit an unsupported `ComponentOutline`
  serialization error on these DSNs).
- **Custom SAFETY rules** in `.kicad_dru` are still not fully conveyed by
  DSN. SES success is not promotion. Shared-layer pour splitting is also a
  modelling gap; always refill and native-guard an additive SES import.
- Autoroute fails on a routing Error or an empty SES; a completed
  autorouter run is not a DRC pass.
