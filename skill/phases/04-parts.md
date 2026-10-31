# Phase 4: Part selection and costed BOM

**Goal:** an approved part for every block, satisfying the 3 BOM sourcing rules (`ohmai check bom-schema`).
Branch `phase/parts`. Check `ohmai check parts`.

## Do
1. For each block, fill a candidates table in `parts/selection.md`: key specs against the requirements
   and safety ratings, package, **price at qty 1/100/1000 with source and date**, stock (source, date),
   lifecycle (active/NRND/EOL), **second source**, and datasheet revision.
2. Only live data. Copy prices from distributor pages or APIs (Nexar/Octopart, LCSC/JLC, DigiKey, Mouser)
   and give the URL and date. Never make one up. When there is no data, write that.
3. Download each chosen part's datasheet (and each non-simple second-source alternate's datasheet) to
   `datasheets/<PART>.pdf` and record its provenance (`mpn`, `lcsc`, `filename`, `url`, `sha256`, `cited_pages`) in
   `datasheets/manifest.yaml` via `ohmai datasheets manifest`. Verify with `ohmai datasheets check` (or fetch missing
   PDFs with `ohmai datasheets fetch --verify`). Raw binary `.pdf` files are gitignored when `datasheets/manifest.yaml`
   is active and blocked by the `pre-push` hook.
   - **Mandatory Phase 4 datasheet & parameter verification for all non-simple parts:** For every non-simple part
     (anything beyond standard R/C/L passives — all ICs, modules, sensors, power converters, protection devices,
     crystals, switches, magnetics, and connectors), verify **both the primary part and its second-source alternate(s)**
     directly against their PDF datasheets and parametric specifications (rendering schematic, pinout, and mechanical
     footprint pages with `pdftoppm -png -r 150` when graphical) **during Phase 4**, before asking for gate sign-off.
     Never trust distributor summary strings alone, and never limit part or second-source selection to the `jlcparts`
     list (search Mouser, DigiKey, Farnell, RS, LCSC, and manufacturer catalogs by parameters, pinout, and footprint).
4. Put the chosen parts in `bom/bom.csv` with status `candidate`. When the owner approves, set `approved`
   and fill `approved_alternates` and `second_source` following the **3 BOM Sourcing Rules**:
   - **Rule 1 — Generic components**: It is fine to write `generic: any maker <spec>...`, **but** the row must
     also list **at least 2 concrete alternatives** (`MPN (Manufacturer, LCSC Cxxxx, $price)`) from **different
     manufacturers** so there are multiple verified low-cost sources across unique manufacturers.
   - **Rule 2 — Non-generic components (true independent core/silicon manufacturer required)**: A row may list a
     same-manufacturer alternate if useful, **but the "at least two different manufacturers" rule applies** —
     every non-single-source fitted part must have at least 2 unique manufacturers across primary + drop-in
     alternates:
     - **No white-label / rebranded / same-core alternates as the sole second manufacturer:** An alternate only
       counts as a second manufacturer if the underlying active silicon, sensor element, or core component is made
       by an independent manufacturer. White-label, rebadged, repackaged, or module-wrapped parts that still depend
       on the same single-source core/silicon manufacturer (e.g., a third-party module built around the same OEM's
       IC die, or a distributor house-brand rebadge of the same OEM component) do **not** count as an independent
       second manufacturer and must be classified under **Rule 3 (`single-source`)** (while still noting the
       secondary module/packager in `notes` if useful for PCBA assembly).
     - **Cross-distributor second sources count:** Do not mark a component `single-source` merely because only one
       manufacturer is stocked at JLCPCB (`jlcparts`). If a true pin-for-pin drop-in from a second independent
       manufacturer is available from Mouser, DigiKey, Farnell, or RS (`MPN (Manufacturer, Mouser/DigiKey PN)`),
       record it as a valid Rule 2 second source.
     - Alternates flagged `different footprint`, `separate footprint`, `out of stock`, `white-label`, or
       `same silicon` do not count as drop-in second manufacturers.
   - **Rule 3 — Single-source components (strictly gated & highlighted)**: Don't make single-source an easy
     decision — verify other options first!
     - **Commodity / standard-category smell test:** Standard categories (RJ45 MagJacks, USB-C receptacles,
       board-to-board/wire headers, crystals, tactile switches, standard buck/LDO regulators, ESD/TVS diodes,
       bridge rectifiers) almost always have drop-in second sources. Before claiming `single-source` on any such
       part, search `jlcparts` and major distributors by **footprint + pin count + function** (not just MPN prefix).
     - **Phase 4 datasheet & parameter verification across all non-simple parts:** For every non-simple component
       (and its proposed second source), verify the actual PDF datasheet parameters, internal schematic/topology,
       pinout, and footprint during Phase 4 (rendering graphical diagrams with `pdftoppm -png -r 150`) rather than
       relying on catalog description strings or limiting searches to JLCPCB's list.
     - A component may only be marked `single-source` if `second_source` / `notes` explicitly documents:
     (a) `single-source (D-xxx)` citing the owner decision, AND
     (b) `checked: ...` listing the concrete candidate MPNs / manufacturers searched and the specific technical,
         footprint, or spec reason each was rejected (or why the IC is architecture-defining with no pin-compatible
         clone). Lazy `"none named"` or `"single source"` without naming searched candidates FAILS `ohmai check bom-schema`.
     `ohmai check bom-schema` and `ohmai check bom-lock` always emit a **`SINGLE-SOURCE COMPONENTS (N)`** highlight
     block so single-source risks are visible at every gate.
4b. **MCU pin budget (hard gate, like the BOM sourcing rules).** Once the MCU MPN and package are picked,
   update `parts/mcu_pin_budget.yaml` from the datasheet pinout (cite the page in `parts/selection.md`):
   ```yaml
   mcu: MCU-TSSOP20        # `none` if the board has no MCU
   package: TSSOP-20
   total_pins: 20
   reserved:                 # counts or pin lists; power, ground and swd are required keys
     power: [VDD, VDDA]
     ground: [VSS]
     reset: [NRST]
     boot: [BOOT0]
     swd: [PA13, PA14]
     fixed_serial: [PA9, PA10]
   required_nets:            # kind: control | adc | ui | debug | comm | pwm | io | other
     - {net: AMP_EN, kind: control}
     - {net: VBUS_SENSE, kind: adc}
   min_spare: 1              # optional
   # free_gpio: 11           # optional override when pins are multi-function (explain in selection.md)
   ```
   `ohmai check mcu-pin-budget` FAILs on a shortfall or an incomplete budget, WARNs if an MCU is mentioned
   but the file is missing, and SKIPs when no MCU is mentioned. Also check that ADC nets land on ADC-capable
   pins. Lesson: a small TSSOP-20 MCU has ~11 free GPIO; a ~16-net design was only caught in Phase 7.
5. Give a total BOM cost at the target quantity, with each estimate marked as one.

## Missing KiCad library parts (the OhmAI library tool; you run it, not the owner)
A part missing from the pinned KiCad libraries is a tool problem. `ohmai check lib-refs` lists it; you fix it
with `ohmai kicad-libs` before Phase 7 draws the sheet. Never ask the owner to draw, edit or supply a symbol or
footprint, and never hand-write one (no one-off `gen_<part>.py` as the deliverable). Order:
1. **Stock first, by package drawing, not by name.** The footprint must exist in the pinned tag and match the
   datasheet land pattern (pitch, pad size, row span, exposed pad); note the page in the BOM `notes`
   (e.g. TI RSN0032B -> `Package_DFN_QFN:VQFN-32-1EP_4x4mm_P0.4mm_EP2.8x2.8mm`).
2. **Vetted export.** Sources: the manufacturer's KiCad files, or Ultra Librarian / SnapEDA *manufacturer-
   verified* parts. Nothing else (no random GitHub libraries, no hand-drawn pin maps).
   - `ohmai kicad-libs fetch <MPN> --vendor ti|ul|st|analog|microchip|nxp|snapeda [--base B] [--package P] [--pins N]`
     prints the download steps and records `pending-download` in `hardware/lib/manifest.yaml`. Terms and
     captchas are a human click: ask the owner for that one step (link + "save the zip to
     `hardware/lib/downloads/`") and keep working. Never accept terms for them.
   - `ohmai kicad-libs import <MPN> --from <zip|dir|.kicad_sym|.kicad_mod> --source-url <URL> [--package P]
     [--name SYMBOL] [--footprint LIB:FP] [--datasheet datasheets/<pdf> --pages a-b] [--replace]`
     takes the KiCad v6+ files from the export, merges the symbol into `hardware/lib/OhmAI_Project.kicad_sym`,
     copies footprints to `OhmAI_Project.pretty/` and 3D models to `OhmAI_Project.3dshapes/`, adds the
     `${KIPRJMOD}` rows to `sym-lib-table` / `fp-lib-table` (backed up), and writes provenance (URL, sha256,
     date, package, pin count) to `hardware/lib/manifest.yaml`. It refuses to overwrite without `--replace`
     and keeps a `.bak-<stamp>` copy.
3. **Verify against the datasheet.** `--datasheet` on import, or `ohmai kicad-libs verify <MPN> --datasheet
   <pdf> --pages a-b`, requires every symbol pin number + name on one line of the pin-functions table
   (pdftotext). Clean -> status `verified`; any mismatch -> exit 1, fix the source, never the check.
4. **Stopgap only while the download waits:** an existing datasheet-transcribed symbol may be registered with
   `import ... --status provisional --note "stopgap: <script>"`. It stays `provisional` (shown by
   `ohmai kicad-libs list`) until a vetted export replaces it with `import --replace`. Footprints are never
   generated: stock (step 1) or the vendor file only.
5. `ohmai check lib-refs` (and `kicad-cli sym export svg hardware/lib/OhmAI_Project.kicad_sym`) must pass
   before the sheet that uses the part is drawn. `ohmai kicad-libs tables` keeps the `${KIPRJMOD}` rows.

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
`ohmai check bom-schema` must pass, and `ohmai check mcu-pin-budget` must PASS (or SKIP for a board with no MCU).

## Autonomous passive package adjustments (Phases 7–8)
Standard passives (`R`, `C`, `L` in `0402`–`0805` / `2016`–`3030`) approved in Phase 4 may be resized autonomously
during Schematic or Layout via `ohmai passives swap <REF> --package <PKG>` when required for courtyard fit, escape
routing, or critical loop length, provided `ohmai check constraints` passes (effective capacitance at DC bias,
voltage/power/current/Isat/DCR ratings, JLCPCB stock & basic-part preference) and the swap is logged in `bom/bom.csv`
and `STATUS.md`.
