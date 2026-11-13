<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
# Library import (`ohmai kicad-libs fetch|import|verify|list`)

Missing KiCad parts are an agent tool problem. The agent fetches, imports and verifies them end-to-end.
The owner does nothing: no downloads, captchas or vendor terms.

| Step | Command | Result |
|---|---|---|
| Find gaps | `ohmai check lib-refs` | unresolved `lib_id` / footprints |
| Stock? | compare land pattern with the pinned tag | use the stock footprint, note the datasheet page in the BOM |
| Fetch | `ohmai kicad-libs fetch <MPN> --name <SYM> --package <P> --pages a-b --pin-types [--footprint LIB:FP] [--replace]` | LCSC/EasyEDA symbol + footprint + 3D in `hardware/lib/OhmAI_Project.*`, table rows, manifest, datasheet check |
| Direct file | `ohmai kicad-libs fetch <MPN> --url <manufacturer .zip/.kicad_sym/.kicad_mod>` | saved to `hardware/lib/downloads/`, imported |
| On disk | `ohmai kicad-libs import <MPN> --from <file|zip|dir> --source-url <URL>` | imported |
| Verify | `ohmai kicad-libs verify <MPN> --datasheet <pdf> --pages a-b` | `verified` or exit 1 |
| Review | `ohmai kicad-libs list` | status per MPN |

## Sources (agent-automatable first)
| Source | How | Automatable |
|---|---|---|
| KiCad official libs (pinned tag) | `ohmai kicad-libs install/tables` | yes |
| LCSC / EasyEDA | `easyeda2kicad --full --lcsc_id=C…` (subprocess only, AGPL-3.0; not bundled), API `easyeda.com/api/products/<C…>/components` | yes, no login |
| Manufacturer direct file / mirror | `--url` (HTTP GET with a plain User-Agent) | yes when the URL is a file |
| Ultra Librarian, SnapEDA, TI/ADI/ST CAD portals | `--source portal` probe | no: login/terms/captcha walls (HTTP 302/403 or HTML); exits 75 |

Trust model: the EasyEDA/LCSC library is LCSC-drawn, not manufacturer-signed, so it is never trusted by
itself. `verified` needs the datasheet pin check (every pin number + name on one pin-table line); footprints
are summarised (pad count, largest pad) for a land-pattern comparison, and the stock KiCad footprint stays
preferred when it matches (`--footprint`). The LCSC number comes from `bom/bom.csv` `supplier_pn` and the
EasyEDA title must equal the MPN.

Exit codes: 0 ok, 1 datasheet mismatch, 75 source blocked or network (retry another source), other = usage.
Statuses: `imported`, `verified`, `provisional` (stopgap until `fetch --replace`).

Limits: the pin check is a line-pairing heuristic on `pdftotext -layout` (BGA ball maps and multi-column
tables may need narrower `--pages`); `--pin-types` maps datasheet `O` to `output` (review open-drain pins);
land patterns are not yet compared numerically with the datasheet.

## Using imported parts in generated sheets
- `ohmai schematic build` resolves a `lib_id` from the pinned stock libraries first. If the library isn't
  there, it falls back to the project's `hardware/lib/<lib>.kicad_sym`, for example
  `OhmAI_Project:TPS63001`.
- Symbols indented with spaces (easyeda2kicad style) are accepted as well as tab-indented ones.
- 3D model paths in imported footprints are always rewritten to
  `${KIPRJMOD}/lib/<lib>.3dshapes/<file>`. Absolute paths and temp paths such as `/tmp/...` are never kept.
  Stock `${KICAD*_3DMODEL_DIR}` paths stay as they are.
