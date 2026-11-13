<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
# Freerouting run log template

Copy an entry per invocation into the project’s `out/routing/` notes or into
this file when keeping long-lived evidence in the OhmAI tree. Never infer
DRC success from an SES file or a Java exit 0. Missing measurements stay
explicit (pending / not measured), never written as zero.

`ohmai layout autoroute` already appends `out/routing/freerouting.jsonl`, a
Markdown index and per-run DSN/SES/tail snapshots. This template is the
human-readable companion and the checklist for an upstream report. Historical
OhmAI evidence: [freerouting-log.md](freerouting-log.md). How to run the
router: [ROUTER-GUIDE.md](ROUTER-GUIDE.md).

## Entry template

```
## FR-YYYYMMDD-NN — short title

- Date (local):
- Board (anonymized label) / copper commit / docs commit:
- Freerouting version (jar sha256) / JDK (java -version):
- Command (exact argv, including FREEROUTING_JAVA if set):
- Scope: --nets / --exclude-nets / pass count / optimizer passes / timeout / threads:
- Outcome: completed / routing Error / empty SES / crash / timeout
- SES wires / vias (by layer, including any non-signal layers):
- `non_signal_layer_wires`: yes/no (fail the run if yes)
- DRC: pending, or path to --drc-before / --drc-after JSON
- Hard gates (after refill): shorting, crossings, hole_clearance, connection
  width (must be 0); island / unconnected counts vs KiCad; SAFETY and
  non-zone clearance vs baseline; any net worse?
- Promoted? yes/no and why
- Crashes / stack signature:
- Rule violations or DSN modelling gaps (SAFETY, shared-layer pours, other):
- Repro artifacts path (DSN, SES, stdout/stderr tail, JSONL line, board copy):
- Upstream candidate? yes (crash / clear bug) / no / needs reduction
```

## Upstream-report checklist

Do not file or upload anything without owner approval.

- [ ] Exact Freerouting jar version and sha256, JDK version, OS, and command line
- [ ] Minimal repro DSN (or a reduced board) that still shows the failure
- [ ] Expected versus actual behaviour, with the stack signature if it crashed
- [ ] SES / log / screenshot attachments, with paths and hashes
- [ ] Attachments checked for private project names, personal emails and
      secrets; anonymize before sharing
- [ ] Note OhmAI-side workarounds (plane reservations, `coalesce_fixed_chains`,
      `--nets` scoping, `non_signal_layer_wires` fail) so upstream can tell
      tool bugs from our filters
- [ ] Search existing upstream issues (for example freerouting#721) before drafting
- [ ] Draft title and body only; filing status stays “draft” until the owner
      approves
