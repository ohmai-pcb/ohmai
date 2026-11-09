<!-- SPDX-License-Identifier: Apache-2.0 -->
# RFQs and supplier questions

One file per supplier question or RFQ: `rfq/<supplier>-<topic>.md`. Each file is a **standalone vendor
document** that the owner sends as-is:
- Full context in the file itself: part numbers, quantities, specs with units, drawings attached by name.
- No internal tags, decision IDs, branch names, repo paths, TODOs or agent notes. `ohmai check rfq`
  enforces this.
- The agent drafts the RFQ and the owner sends it. Log the answer (with date) in `docs/decisions.md` or the
  constraints file it affects.
