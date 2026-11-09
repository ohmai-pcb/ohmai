<!-- SPDX-License-Identifier: Apache-2.0 -->
# Phase 3: Safety and compliance plan

**Goal:** know the ratings and spacing before parts freeze. Branch `phase/safety`. Check `ohmai check safety`.

## Do
1. List the hazards: voltages above SELV, stored energy, heat, user-touchable parts, mains or battery.
2. List the candidate **standards**, each with a reason and the clauses used (e.g. IEC 62368-1 for
   ICT/AV). Cite where each number comes from. You do not decide compliance. Say "candidate" and let the
   owner or a lab confirm.
3. Write the **creepage/clearance table** per net-class pair: working voltage, insulation type, pollution
   degree, mm values, standard + table. This table becomes DRC rules in phase 8.
4. Write the **component rating/derating rules** (voltage, power, temperature), each with a source.
5. Cover protection (fuse, reverse polarity, ESD/surge on external connectors) and the certification path.
6. Fill **Target markets and legislation** for each market in STATUS.md `markets:`. For the EU, decide
   per act and cite the scope clause:
   - GPSR (EU) 2023/988 (consumer products; complements CE acts for risks they do not cover). Art. 9(5)
     type/batch/serial number, 9(6) manufacturer name, postal and electronic address, 9(7) instructions
     and safety information in a language the consumers of that Member State understand.
   - EMC 2014/30/EU (apparatus): Art. 7(5)-(7) identification, address, instructions; Art. 18 use
     precautions, residential-area restriction, intended purpose; Art. 15-17 EU declaration of
     conformity and CE marking.
   - LVD 2014/35/EU only if rated 50-1000 V AC or 75-1500 V DC (Art. 1). A 5 V USB board is outside it.
   - RED 2014/53/EU only for radio equipment (then Art. 10(8) instructions, 10(9) DoC copy or simplified
     DoC, and RED replaces EMC/LVD for that product).
   - RoHS 2011/65/EU (EEE): Art. 7 DoC, CE marking, identification, address.
   - WEEE 2012/19/EU: Art. 14(4) and Annex IX crossed-out wheeled bin (EN 50419:2022), Art. 14(2) user
     information.
   - CE marking rules: Reg. (EC) 765/2008 Art. 30; economic operator in the EU: Reg. (EU) 2019/1020 Art. 4.
   Other markets (UK, US, ...): list the acts with sources; OhmAI has only rough text rules for them.
7. Fill **Required manual warnings**: every MW-xx from STATUS.md plus any hazard found in this phase.
   Draft `docs/manual.md` §Safety information and §Conformity from it (template in the OhmAI template).

## Gate
The owner approves `docs/safety.md` (markets, acts, required warnings). Parts selection (phase 4) must meet these ratings.

## Independent review
Before the gate: `ohmai review start safety`, get an independent review at the highest feasible level (skill
section 3a), resolve every finding in `reviews/safety/resolutions.md`; `ohmai check review:safety` must pass.

If this gate's `min_independence` cannot be met, try every better level first and document why each was not
available; only then ask the owner for a waiver (`accepted_independence`, skill section 3a, docs/REVIEWS.md).
