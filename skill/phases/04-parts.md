# Phase 4: Part selection and costed BOM

**Goal:** an approved part for every block, satisfying the 3 BOM sourcing rules (`ohmai check bom-schema`).
Branch `phase/parts`. Check `ohmai check parts`.

## Do
1. For each block, fill a candidates table in `parts/selection.md`: key specs against the requirements
   and safety ratings, package, **price at qty 1/100/1000 with source and date**, stock (source, date),
   lifecycle (active/NRND/EOL), **second source**, and datasheet revision.
2. Only live data. Copy prices from distributor pages or APIs (Nexar/Octopart, LCSC/JLC, DigiKey, Mouser)
   and give the URL and date. Never make one up. When there is no data, write that.
3. Download each chosen part's datasheet to `datasheets/<PART>.pdf` and record its provenance (`mpn`, `lcsc`,
   `filename`, `url`, `sha256`, `cited_pages`) in `datasheets/manifest.yaml` via `ohmai datasheets manifest`.
   Verify with `ohmai datasheets check` (or fetch missing PDFs with `ohmai datasheets fetch --verify`). Raw binary
   `.pdf` files are gitignored when `datasheets/manifest.yaml` is active and blocked by the `pre-push` hook.
4. Put the chosen parts in `bom/bom.csv` with status `candidate`. When the owner approves, set `approved`
   and fill `approved_alternates` and `second_source` following the **3 BOM Sourcing Rules**:
   - **Rule 1 — Generic components**: It is fine to write `generic: any maker <spec>...`, **but** the row must
     also list **at least 2 concrete alternatives** (`MPN (Manufacturer, LCSC Cxxxx, $price)`) from **different
     manufacturers** so there are multiple verified low-cost sources across unique manufacturers.
   - **Rule 2 — Non-generic components**: A row may list a same-manufacturer alternate if useful, **but the
     "at least two different manufacturers" rule applies** — every non-single-source fitted part must have at
     least 2 unique manufacturers across primary + drop-in alternates (alternates flagged `different footprint`,
     `separate footprint`, or `out of stock` do not count as drop-in second manufacturers).
   - **Rule 3 — Single-source components (strictly gated & highlighted)**: Don't make single-source an easy
     decision — verify other options first! A component may only be marked `single-source` if `second_source` /
     `notes` explicitly documents:
     (a) `single-source (D-xxx)` citing the owner decision, AND
     (b) `checked: ...` listing the concrete candidate MPNs / manufacturers searched and the specific technical,
         footprint, or spec reason each was rejected (or why the IC is architecture-defining with no pin-compatible
         clone). Lazy `"none named"` or `"single source"` without naming searched candidates FAILS `ohmai check bom-schema`.
     `ohmai check bom-schema` and `ohmai check bom-lock` always emit a **`SINGLE-SOURCE COMPONENTS (N)`** highlight
     block so single-source risks are visible at every gate.
5. Give a total BOM cost at the target quantity, with each estimate marked as one.

## Optional: parametric search (jlcparts)
`ohmai parts search <words> [--category C] [--package P] [--basic] [--min-stock N] [--qty Q]` searches a
local jlcparts database (github.com/yaqwsx/jlcparts, MIT; the `cache.sqlite3` it builds, current `source-db-v2` format or the
older partLib schema, pointed to by `OHMAI_JLCPARTS_DB` or `--db`). Delisted parts are left out. Not required; nothing else depends on it.
- Use it to find candidates (JLC basic parts first, then stock). A hit is a lead, not a fact: stock and
  price are from the snapshot date shown. Open the printed LCSC page and copy the live price, stock, URL
  and date into `parts/selection.md`, as rule 2 says. Check the datasheet, not the catalogue description.
- The database is a tier-1 lock entry (`jlcparts`, versioned by file date + hash), so a project that uses
  it records which snapshot the candidates came from. ohmai never downloads or updates it.

## Gate
The owner approves each part under the 3 BOM sourcing rules (including any highlighted single-source parts).
`ohmai check bom-schema` must pass.

## Autonomous passive package adjustments (Phases 7–8)
Standard passives (`R`, `C`, `L` in `0402`–`0805` / `2016`–`3030`) approved in Phase 4 may be resized autonomously
during Schematic or Layout via `ohmai passives swap <REF> --package <PKG>` when required for courtyard fit, escape
routing, or critical loop length, provided `ohmai check constraints` passes (effective capacitance at DC bias,
voltage/power/current/Isat/DCR ratings, JLCPCB stock & basic-part preference) and the swap is logged in `bom/bom.csv`
and `STATUS.md`.
