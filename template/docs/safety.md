<!-- ohmai:template: fill in, then delete this line. Gate: owner approves before parts freeze (phase 3). -->
# Safety and compliance plan: {{project_name}}

Not legal advice. The agent lists candidate standards with sources; the owner (or a compliance lab) decides.

## Target markets and legislation
From STATUS.md `markets:`. One row per act per market; say "not applicable" with the reason.
| Market | Act | Applies? | Why / scope clause | Marks and documents | Source |
|--------|-----|----------|--------------------|---------------------|--------|

## Hazards
| Hazard | Where | Voltage / energy | Mitigation |
|--------|-------|------------------|------------|

## Applicable standards (candidates, with source)
| Standard | Why it applies | Clause(s) used | Source |
|----------|----------------|----------------|--------|

## Insulation, creepage and clearance
| Between (net class A ↔ B) | Working voltage | Insulation type | Pollution degree | Clearance (mm) | Creepage (mm) | Standard + table |
|---------------------------|-----------------|-----------------|------------------|----------------|---------------|------------------|

These become DRC rules in `hardware/*.kicad_dru` (phase 8).

## Component ratings and derating
| Part class | Rule (e.g. caps ≤ 80 % rated V, resistors ≤ 50 % rated P) | Source |
|------------|------------------------------------------------------------|--------|

## Protection
Fusing, reverse polarity, ESD/surge on external connectors, thermal shutdown, touch current.

## Required manual warnings
Every hazard or limitation the user must be told about, with the ID registered in STATUS.md "Manual
warnings". Each one goes into `docs/manual.md` §Safety information (checked by `manual-warnings` and `manual`).
| ID | Warning (hazard, consequence, how to avoid) | Source (phase, doc) |
|----|---------------------------------------------|---------------------|

## Certification path
Self-declaration vs lab, pre-compliance tests, marks, documentation to keep.
