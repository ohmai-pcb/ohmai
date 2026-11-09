<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- ohmai:template docs/tool-issues.md -->
# Project tool issues and limits — mandatory close-out report

Complete before fab; refresh at final bring-up/project close-out. This report is
required for every project and cannot be marked not applicable. Inventory **every
tool actually used**, including tools with no observed issues. Remove the template
marker only after reviewing completeness. Unknown versions/repros must be stated
explicitly, never invented. This document drafts issues; it does not authorize filing.

- Project / final board revision / commit:
- Report date / reviewer role:
- Scope of validation and remaining limitations:
- Evidence bundles and preserved run logs:

## Tool inventory

| Tool / component | Used or not used | Exact version / hash, or unavailable reason | Evidence | Issues / explicit none observed |
|---|---|---|---|---|
| OhmAI | | | | |
| Freerouting and JDK | | | | |
| KiCad / kicad-cli / pcbnew | | | | |
| Library-import tool | | | | |
| JLC / LCSC sourcing helpers | | | | |
| Other generators, CAD/MCAD, Git/LFS, checks, exporters, test/bring-up tools | | | | |

## Issue entry — repeat for every bug or limit

- ID:
- Tool and exact version / environment:
- Severity: critical / high / medium / low (justify):
- Symptom and expected versus actual behavior:
- Minimal repro commands, inputs, and artifact paths / hashes:
- Reduction status (standalone minimal repro or remaining reduction needed):
- Project impact (blocked work, safety/manufacturing risk, time, affected nets/outputs):
- Workaround actually used (or none):
- Fixed? yes/no/partial; exact fixing commit and regression test (or unknown):
- Upstream repository / existing issue if known:
- Upstream-ready title:
- Upstream-ready body:
  - Tool/version/environment:
  - Repro steps and attached artifact paths:
  - Expected result:
  - Actual result / stack signature:
  - Project impact and workaround:
  - Fix status / commit / regression evidence:
- Filing status: draft only / owner-approved / filed link. Do not send or upload without approval.

## Close-out checklist

- [ ] Inventory includes every tool used; tools without issues explicitly say none observed.
- [ ] Every bug/limit has severity, impact, repro artifacts, actual workaround, fix status/commit and upstream title/body.
- [ ] Freerouting JSONL and other tool logs reviewed; missing measurements are explicit, not zero.
- [ ] Repro artifacts retained and checked for private/project data before any upstream sharing.
- [ ] All unresolved manufacturing/safety issues remain visible in the project release decision.
- [ ] Report refreshed for final commit and reviewed by the owner; no upstream filing was assumed.
