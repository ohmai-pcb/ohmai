<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
<!-- ohmai:template: fill in, then delete this line. Drafted from phase 3, finalized in phase 12. Checked by `ohmai check bringup` (manual). -->
# User instructions: {{project_name}}

Not legal advice. This is the structure OhmAI requires; the owner (or a compliance lab) confirms the
legal content for each target market in STATUS.md `markets:`. The rules each section serves are in the
table at the end. Every warning ID `MW-xx` from STATUS.md "Manual warnings" must appear in this file.

## Product identification
Product name, type/model number, batch or serial number and where it is printed (on the board, or on the
packaging/accompanying document if the board is too small), hardware revision, a photo or drawing.

## Manufacturer and contact
Manufacturer name, registered trade name or trade mark, postal address, electronic address (e-mail or
web form). If the manufacturer is outside the market: the responsible economic operator (importer or
authorised representative) established there, with postal and electronic address.

## Intended use
What the product is for, who may use it, and foreseeable misuse that is not allowed. If EMC compliance
is not ensured in residential areas, say so here (and on the packaging).

## Ratings and specifications
Supply voltage and current, interface ratings, measurement range and accuracy, operating and storage
temperature, humidity, IP rating. Every number traces to docs/requirements.md or a test report.

## Safety information
One entry per warning, with its ID, e.g.:

- **MW-xx: WARNING** – hazard, consequence, how to avoid it.

## Installation and use
Assembly (soldering), connection, power-up, the specific precautions needed to stay EMC-compliant
(cables, enclosure, grounding), normal operation, and troubleshooting.

## Maintenance and cleaning
What the user may do, what needs service, cleaning method, firmware updates.

## Disposal
Crossed-out wheeled bin symbol (WEEE) and its meaning: do not dispose of as unsorted municipal waste,
use separate collection; where the symbol is printed if the board is too small for it.

## Conformity
Marks on the product (e.g. CE), the legislation applied, and where the EU declaration of conformity
(or the equivalent for the market) is available. For radio equipment: the simplified declaration and
frequency bands/maximum power.

## Language versions
Languages supplied per target market, and the revision/date of this document.

## Sources (what each section serves)
| Section | Rule | Markets |
|---------|------|---------|
| Product identification | GPSR (EU) 2023/988 Art. 9(5); EMC 2014/30/EU Art. 7(5); RoHS 2011/65/EU Art. 7(g) | EU |
| Manufacturer and contact | GPSR Art. 9(6); EMC Art. 7(6); RoHS Art. 7(h); Reg. (EU) 2019/1020 Art. 4 (economic operator established in the EU) | EU |
| Intended use, Installation and use | EMC Art. 18(1)-(3) | EU |
| Safety information, language | GPSR Art. 9(7); EMC Art. 7(7) | EU |
| Disposal | WEEE 2012/19/EU Art. 14(2)(e), 14(4), Annex IX; EN 50419:2022 | EU |
| Conformity | CE: Reg. (EC) 765/2008 Art. 30; EMC Art. 15-17; RoHS Art. 7(c); RED 2014/53/EU Art. 10(8)-(9) if radio | EU |
