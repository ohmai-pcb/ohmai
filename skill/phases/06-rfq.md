# Phase 6: Supplier questions and RFQs

**Goal:** answers to what datasheets do not say: custom magnetics, cables, enclosures, quotes, lead times.
Branch `phase/rfq`. Check `ohmai check rfq`. Skip this phase when no questions are needed, and record
that in STATUS.md.

## Do
1. One file per supplier/topic: `rfq/<supplier>-<topic>.md`.
2. Write each one as a **standalone vendor document**: who you are (the owner's company/name, as given by
   the owner), what you need, quantities, specs with units and tolerances, attachments by file name, and
   the questions as a numbered list. Include a requested reply date.
3. Leave out internal tags, decision IDs, repo paths, branch names, TODOs and agent wording.
   `ohmai check rfq` enforces this.
4. You never send anything. The owner sends the RFQs.
5. Log each answer (with date and sender) in the constraints file or decisions.md it affects.

## Gate
The owner reviews and sends the RFQs. The phase closes when the answers that block the schematic are in.
