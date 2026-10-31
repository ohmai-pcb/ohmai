# Library import (`ohmai kicad-libs fetch|import|verify|list`)

Missing KiCad parts are solved by the OhmAI agent with this tool. The owner's only step is a vendor
captcha/terms click when asked.

| Step | Command | Result |
|---|---|---|
| Find gaps | `ohmai check lib-refs` | unresolved `lib_id` / footprints |
| Stock? | compare land pattern with pinned tag | use stock footprint, note datasheet page in BOM |
| Download steps | `ohmai kicad-libs fetch <MPN> --vendor ti --base <GPN> --package <PKG> --pins <N>` | steps printed, manifest `pending-download`, `hardware/lib/downloads/` |
| Import | `ohmai kicad-libs import <MPN> --from hardware/lib/downloads/<zip> --source-url <URL> --package <PKG> --datasheet datasheets/<pdf> --pages a-b [--name S] [--footprint LIB:FP] [--replace]` | symbol/footprint/3D in `hardware/lib/OhmAI_Project.*`, table rows, manifest |
| Verify | `ohmai kicad-libs verify <MPN> --datasheet <pdf> --pages a-b` | `verified` or exit 1 with mismatches |
| Stopgap | `ohmai kicad-libs import <MPN> --from <file> --status provisional --note "stopgap: ..."` | stays `provisional` until replaced |
| Review | `ohmai kicad-libs list` | status per MPN |

Statuses: `pending-download`, `imported`, `verified`, `provisional`.
Vetted sources only: manufacturer KiCad files, Ultra Librarian, SnapEDA manufacturer-verified. Legacy
`.lib`/`.mod` are not imported. The tool never generates footprints or pin maps.

Limits of this first cut: the datasheet check is a line-pairing heuristic over `pdftotext -layout` (name and
number on one line); BGA ball maps and multi-column pin tables may need `--pages` narrowed. Footprints are not
yet compared with the datasheet land pattern automatically (manual check, note the page). Downloads are not
automated (captcha/terms).
