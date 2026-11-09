<!-- SPDX-License-Identifier: Apache-2.0 -->
# EVB comparison

For every power IC whose constraints file has `evb_reference`, create `<PART>.md` here before the
schematic gate. Compare the schematic with the vendor EVB or reference design, part by part:

| Vendor ref | Function | Vendor value / MPN | Ours | Same? | Reason if different (cite) |
|------------|----------|--------------------|------|-------|----------------------------|

`ohmai check evb-compare` fails while a file is missing or still has the template marker.
