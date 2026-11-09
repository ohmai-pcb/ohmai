<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- ohmai:template: fill in, then delete this line. Gate: the owner locks this file (phase 1). -->
# Requirements: {{project_name}}

Idea (owner's words): {{idea}}

Rules: SI units with tolerances. Every number has a source (owner, standard, or datasheet). Unknowns go to
STATUS.md → Open questions, not into this table as guesses.

## Must (the board fails without these)
| ID | Requirement | Value / limit | Unit | Source | Verify by |
|----|-------------|---------------|------|--------|-----------|
| R-01 | | | | owner | test / review / analysis |

## Wish (nice to have, traded off in phase 2)
| ID | Wish | Value | Priority (1-3) |
|----|------|-------|----------------|
| W-01 | | | |

## Interfaces
Power in, power out, connectors, buses, user I/O, firmware/debug access.

## Environment
Ambient temperature range, humidity/condensation, IP rating, vibration, altitude, indoor/outdoor, lifetime.

## Cost and volume
| Item | Target | Note |
|------|--------|------|
| Quantity (proto / production) | | |
| BOM cost per board at qty | | |
| PCB size / layers limit | | |
| Fab/assembly house | | |

## Target markets (required, input to phase 3)
| Market | Sold / used there? | Note |
|--------|--------------------|------|
| EU (CE) | | |
| UK (UKCA) | | |
| US (FCC) | | |
| Other | | |

Copy the answer into STATUS.md `markets:`. Markets marked no are not applicable: no marks, rules or
manual content for them.

## Safety and regulatory (input to phase 3)
Voltages present (SELV or not), user-touchable parts, consumer or professional users, standards the owner knows of.

## Out of scope
