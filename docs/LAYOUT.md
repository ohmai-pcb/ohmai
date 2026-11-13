<!-- SPDX-License-Identifier: Apache-2.0 -->
# Layout steps (phase 8)

Project settings live in `hardware/layout.yaml` (or the file named by ohmai.yaml `layout.config`). Board rules
(net classes, via size, hole and edge minimums) come from the board's `.kicad_pro`; the file may override them.
Unknown keys are errors. Coordinates are KiCad board mm. Nothing here has defaults tied to one board: every net
name, reference, layer and area comes from the project file.

## Escape room (`ohmai check escape-room`, `ohmai layout fit`)

Placement rules for fine-pitch QFN/QFP parts, checked on the placed board before routing. They come from a
4-layer board whose autorouter plateaued at 22-75 open connections until the placement around its QFN-60 was
reworked (reference-board D-021); every rule is one of the things that rework had to fix.

| Rule | Fails when |
|---|---|
| toe-gap | the copper gap from a side's pin toes to another part's pad is below via + 2 x clearance (one escape via) |
| flash-facing | a two-column flash has its columns across the line to the MCU, or its pin order is mirrored (links cross) |
| edge-lane | a small part sits between a cap of the MCU's column and the edge / castellation pad it faces |
| ring-vias | a plane / stitching via sits in the escape ring (toes .. toes + via + 2 x clearance); before routing only |
| inductor | an inductor's switch-node pad is its far pad from the IC pins it connects to |
| ring-fill | courtyards fill more than `ring_fill_max` (0.6) of the `ring_fill_width` (2 mm) ring around the MCU |
| side-capacity | a QFN side has more signal pins to escape than outward lanes + via sites under the body (estimate) |

```yaml
escape_room:
  parts: [{ref: U1, exempt: [C16], skip_nets: ["^(DVDD|VREG_.*)$"]}]   # exempt: datasheet-placed parts
  flash: [{ref: U2, to: U1}]
  inductors: [{ref: L1, to: U1, nets: [VREG_LX]}]
  edge_parts: [J4]
```

Geometry is axis-aligned boxes read from the .kicad_pcb (exact at 0/90/180/270 degrees, conservative otherwise);
side capacity is an estimate (lanes in a band past the toes, staggered via rows between heels and exposed pad).

`ohmai layout fit REF --at x,y[,rot]` says whether a part fits there (courtyard overlaps on its side, board edge
with `--edge-margin`); `--scan [--radius R --rots 0,90]` lists the nearest free spots; `--rules` re-runs the
escape-room rules with the part moved; `--ignore` leaves out parts you are about to move as well.

## Sub-second placement check (`ohmai check placement`) & pre-review `layout_checklist`

`ohmai check placement` (also run as part of `ohmai check layout` and enforced before `ohmai review start layout`)
evaluates `critical_loops:`, `keepouts:`, `layout_checklist:`, and `pour:` directly against `.kicad_pcb` in `< 1 s`:

- **`critical_loops`**: Checks maximum edge-to-edge pad distance (`max_mm`) and optional `same_layer: true` between pin/pad
  endpoints (defined in `hardware/layout.yaml` or `parts/constraints/*.yaml`), e.g. `C_IN -> VIN/PGND <= 2.5 mm`,
  `L_SW -> SW <= 3.0 mm`, decouplers `<= 3.5 mm`.
- **`keepouts`**: Checks rectangular keepout zones (`no_copper`, `no_tracks`, `no_vias`, or relative `ref` under-body
  keepouts such as under a shielded inductor) across specified copper layers.
- **`layout_checklist`**: Machine-verifiable datasheet layout checklist items in `parts/constraints/*.yaml` or
  `hardware/layout.yaml` (`id`, `rule`, `verified: true`, `cite: {page: N}`).
- **Multi-island courtyards**: Disconnected `fp_rect`/`fp_poly`/`fp_line` loops on `F.CrtYd`/`B.CrtYd` (such as dual-row
  castellated edge strips `J4`) are parsed into separate `courtyards: list[Box]` islands so components placed between the
  two rows pass `ohmai layout fit` without `--ignore-courtyard`.
- **Plane continuity & neck-width (`pourgeom`)**: Checks filled zones for `pieces == 1` and `min_neck >= threshold`,
  reporting the exact `(x, y)` coordinates of the narrowest neck.

## Layout CLI subcommands & `--staged` dry-runs

- `ohmai layout fit <REF> --at x,y[,rot] [--scan] [--rules] [--staged]`
- `ohmai layout fanout [--nets REGEX] [--exclude-pad REF.PAD] [--exclude-near REF.PAD:radius_mm] [--staged]`
- `ohmai layout autoroute [--opt-passes 2] [--timeout 00:10:00]` (hard 3-try router cap on any fixed placement)
- `ohmai layout pour [--net NET --layer LAYER] [--staged | --board PCB] [--section x=MM|y=MM] [--width-at X,Y]`
- `ohmai layout handroute route --nets NET1,NET2 [--budget-seconds 60] [--max-total-expand 2500000] [--max-searches 128] [--staged]`

### Autoroute fixed-copper normalization

The DSN patch joins degree-two chains with identical net, width, layer and fixed
state into a single polyline. Vertices, branches and copper geometry are unchanged.
This avoids Freerouting 2.2.4's search-tree NPE while normalizing split fixed wires (optional on 2.5.0+ where upstream #721 is fixed; `autoroute.dsn.coalesce_fixed_chains`).
Wrapped paths, quoted net names and fractional coordinates are fixed too. SES
progress counts multiline `network_out` copper (implicit route type), excluding
explicit `protect` / `fix` items. Refill and DRC the candidate before promoting;
a completed autorouter run is not a DRC pass.

### Bounded handroute

The time, total expansion and search-count caps are shared across every net and
width attempt. Layout YAML keys are `handroute.budget_seconds`, `max_total_expand`
and `max_searches`; `max_expand` remains the individual-search cap. A path must
join at least two terminal islands to count as progress. JSON reports exhaustion,
stop reasons, unattempted nets, searches and expansions. Budget exhaustion returns
nonzero and any generated copper is partial, not a completed route.

### Fanout selection and protected areas

`--nets '^GND$'` selects only matching pads, overriding the configured escape refs;
it does not require editing `fanout.escape`. `--exclude-pad REF.PAD` skips a source
pad. Repeat `--exclude-near REF.PAD:radius_mm` to protect circular areas: both the
via disk and the entire stub must remain outside. Unknown pads and invalid radii
are errors. YAML equivalents: `fanout.nets`, `exclude_pads` and `exclude_near`.

Foreign-net class clearance is the maximum of the two classes. The IC toe-gap
exception never lowers clearances of 1 mm or more. Custom `.kicad_dru` clearance
rules with footprint selectors are conservatively applied to the whole selected
footprint, even when their condition restricts a narrower pairing. This is not a
full KiCad rule-expression evaluator; always run DRC with the project rules.

### Initial versus predicted connectivity

`initial_islands` describes copper on the input board; `predicted_islands` (also
`remaining_islands` for compatibility) describes the proposed routing result.
Never treat a dry-run prediction of one island as proof that the input board is
already closed: check `initial_islands` or `already_connected_nets`. Dry runs write
no copper, even with `--staged`; `copper_written` states whether this call wrote
proposed copper. The zone model keeps actual filled polygons, including concavities,
instead of treating their enclosing rectangles as copper. Refill and DRC remain
the authority for the written board's connectivity.

The island model matches KiCad connectivity on the stored fill: pads of the same footprint
that touch through a custom pad's primitives (an exposed pad joined to drain legs) are one
island, and vias and through-hole pads reach filled pours on every copper layer, including
inner planes that are not routing layers. On the stereo reference board every net's
`initial_islands` equals a KiCad union-find count after refill. The count uses the fill stored
in the board; refill first if the pours are stale.

Handroute also respects resolved custom footprint safety clearances for both
track and via occupancy. Copper inside a foreign safety envelope is never
reopened as an A* seed merely because an own-net pad or track overlaps it.

### Handroute manufacturing floor and native acceptance gate

Routing widths (including `neck_w` and `alt_widths`) cannot fall below the greater
of 0.127 mm, the board minimum track/connection width, and an explicit class
`min_width`/`min_track_width`. A class `width`/KiCad `track_width` is a preferred width,
not itself a minimum. A corridor below the floor is blocked; the search may take
another corridor or change layers. Diagonal moves cannot squeeze between blocked
orthogonal cells. Terminal stubs enter the copper core rather than merely
clipping an edge: a nominally wide track can otherwise have a much narrower
physical connection. Circle, oval, roundrect and rotated rectangle terminals use
inscribed real pad copper, never the empty corners of placement bounding boxes.
Custom pads are their anchor (circle or rect, from `(options (anchor ...))`) plus every copper
primitive: filled `gr_poly`, `gr_rect`, `gr_circle`, and stroked lines, arcs, outlines and
bezier control polylines, each grown by its stroke width and rotated with the pad. Never the
primitive bounding box. Unsupported pad shapes (trapezoid) remain non-routable terminals rather
than inventing copper. Custom `edge_clearance` constraints also raise both handroute and fanout setbacks.
Via placement uses the **via** radius against tracks and
other vias, including drill-to-drill spacing, rather than the smaller track radius.

Foreign filled power pours are protected using their actual polygons; they must
not be cut into disconnected islands by new tracks. Configured plane nets can
still refill around routes, but are subject to the same final connectivity guard.

The CLI validates a proposal before writing copper: on private board/project/rule
copies it refills zones with `pcbnew`, then runs
`kicad-cli pcb drc --format json --severity-all`. It refuses new sub-floor contacts,
new copper clearance/short/crossing violations, or an increased KiCad unconnected
item count on **any** net (including non-target nets). Existing warnings are not
silently treated as new routing failures. This is an atomic back-off, not a promise
to find a second complete routing solution after a DRC rejection. The result's
`verification` and `rejected_proposal` explain attempted progress; accepted copper
counts remain zero on rejection and `proposal_rejected` is true. Full before/after
DRC JSON and candidate copper are retained in a unique run directory under
`work_dir/handroute-verification/`, even when the proposal is rejected. The
`verification.before_drc`, `after_drc` and `evidence_dir` fields locate the evidence. An existing
staged input is left untouched; a newly requested staged file contains only the
original copper. CLI rejection exits with status 1. Dry-run performs the same
native checks but never writes copper or base/routes JSON files.

`handroute apply` runs the same native gate before promotion and backs up an
existing destination with a timestamped `.bak` copy.

Native validation requires `kicad-cli` and a Python interpreter with KiCad's
`pcbnew` module (the system Python by default; override with
`OHMAI_KICAD_PYTHON`). Missing tools or validation failures reject the proposal;
there is no CLI bypass. Library callers can explicitly request `verify_drc=True`;
it is automatic when resolved board rules declare a positive `min_connection`.
Native refill has a 60-second timeout; full DRC has a 180-second timeout.
Both are separate from the bounded path-search budget.


## Versioned layout change packages

Run from the project root (the board and rules come from the normal layout config):

```sh
ohmai layout apply-changes changes/change-list.json --dry-run
ohmai layout apply-changes changes/change-list.json
```

Both commands validate **and run full native refill/JSON DRC**. Dry-run never writes the live
board. On success apply atomically replaces only the PCB with the measured, refilled candidate,
after a `name.YYYY-MM-DD_HHMM.bak` backup. The project/rules are not changed. Concurrent edits
abort the transaction. Locked footprints and locked edited/deleted copper are refused.

### Schema v1

The machine-readable schema is `ohmai/layout/change-list.schema.json` in the CLI package.
All objects reject unknown keys. All coordinates/dimensions are board mm (KiCad y down),
rotations are degrees; UUIDs are KiCad UUID strings. A package contains:

| Field | Meaning |
|---|---|
| `schema_version` | Exactly `1` |
| `base.commit` | Original local source commit, 7–40 hexadecimal characters, for provenance |
| `base.board_file` | Immutable base PCB snapshot, resolved relative to the change-list file |
| `base.sha256` | Required SHA-256 of that exact snapshot; do not point at the live PCB |
| `operations` | Nonempty ordered list; every operation has a unique descriptive `id` |

The snapshot, not a moving Git HEAD, is the comparison authority; packages work on copies
without Git. Export it from the recorded commit. Include both snapshot and list in handoffs.
Do not change the snapshot/hash merely to bypass a stale-copper conflict: first inspect and
rebase the affected operations against the new board.

| `op` | Required fields besides `id`, `op` |
|---|---|
| `via.add` | `net`, `at:[x,y]`, `diameter`, `drill`, `layers:["F.Cu","B.Cu"]` |
| `via.delete` | `uuid`, `expect` containing **all** the via.add geometry/net fields |
| `track.add` | `net`, `layer`, `start:[x,y]`, `end:[x,y]`, `width` |
| `track.delete` | `uuid`, `expect` containing **all** the track.add geometry/net fields |
| `zone.add` | `net`, `layer`, `polygon:[[x,y],...]`, `priority`, `clearance`, `min_thickness`, `pad_connection`, `island_removal` |
| `zone.edit` | `uuid`, `expect` with at least `net`, `layer`, `polygon` (other zone.add fields optional), `set` containing at least one editable zone setting; `set.polygon` resizes/reshapes. Optional `polygon_tolerance`, or `match: "uuid"` (see below) |
| `zone.delete` | `uuid`, `expect` with at least `net`, `layer`, `polygon`. Optional `polygon_tolerance`, or `match: "uuid"` |
| `footprint.move` | `ref`, `expect:{"at":[x,y],"rotation":degrees}`, `to:[x,y]` (translation only) |

Zone `pad_connection` is `solid`, `thermal` or `none`; `island_removal` is `always` or `never`.
Optional `thermal_gap` and `thermal_spoke` apply to zone add/edit and can be included in `expect`.
`zone.add` and `zone.edit` `set` also accept an optional `name` (KiCad zone name).
Zone edits may set polygon/priority/clearance/min_thickness/pad_connection/island_removal/thermal/name
settings, never net or layer. V1 edits only single-outline zones without holes, straight track
segments and through vias; arcs, blind/buried via edits and complex/multilayer-zone edits fail closed.
Polygon vertices must form a nonzero-area, non-self-intersecting outline; `zone.add` and
`zone.edit set.polygon` are checked when the change list loads, before any KiCad work, and the
error names the operation and the problem (for example `Self-intersection[5 2]`). Deletes require
both UUID and matching expected geometry/net (2 nm numeric tolerance); segment endpoints are ordered.

Zone `expect.polygon` is compared per coordinate within `polygon_tolerance` mm (default 0.005,
at most 0.1), starting at any vertex but with the same winding, so a polygon rounded to 3
decimals still matches. `"match": "uuid"` makes `expect` optional (and allows a partial one,
such as only `net`): the zone is identified by its UUID plus the base-commit fingerprint, so a
zone that was changed or added since the base commit is still refused.

Validation checks actual native copper shapes/layers, enabled layer names, known net names,
zero-length tracks, via annulus, duplicate copper and foreign-net physical overlap. Old same-net
attachments are allowed. Pours may overlap old copper because refill carves their clearances.
Copper UUIDs/geometries are compared with the snapshot (cached zone fills are excluded).
Appended tracks/vias without UUIDs are matched by native geometry with multiplicity, never
treated as unchanged merely because their UUID is absent. Such anonymous items must first be
serialized with persistent UUIDs before they can be addressed by delete/edit operations:
foreign-net overlap/clearance conflicts with added or changed copper are refused and reported
by operation ID, copper UUID and net. Edits/deletes of changed or newly added items are refused.
The native DRC remains authoritative for custom clearances, thermal spokes, plane islands and
all interactions between proposed operations.

A transaction is rejected on **any net's increased unconnected count**, any new illegal copper
or narrow connection, or increased aggregate shorts/crossings/hole-clearance/connection-width/
SAFETY/non-zone-clearance counts. No gate can be waived by a package. Both sides are refilled
with identical project/custom rules and at least the 0.127 mm connection-width floor.
A native refill SIGSEGV is retried at most three times from immutable input; other errors fail
immediately. `refill-attempts.json` retains return codes/stdout/stderr, including crashes.

Every invocation creates a unique `out/layout/apply-changes/` RESULTS-style package with the
**exact apply command**, validation conflicts, the original/proposed PCB and rules, full
before/after JSON DRC and a result. After-DRC evidence is written **before rollback/rejection**;
a tool/refill failure instead preserves the inputs, partial output and failure diagnostic.
Do not hand-edit a board to apply a RESULTS package: use its named apply command.


### Zone add, resize and delete

Zones are ordinary transaction operations: they are validated, dry-run, refilled, DRC-gated
and rolled back exactly like tracks and vias. `ohmai layout zones [--net REGEX] [--json]` lists
the board's copper zones (rule areas are skipped) with UUID, net, layers, name, priority and
outline, and prints a ready-to-paste `expect` for every editable zone (single layer, one outline,
no holes); other zones show why they cannot be edited. Example package (fictional board):

```json
{"schema_version": 1,
 "base": {"commit": "abcdef0", "board_file": "board.kicad_pcb", "sha256": "<sha256 of base>"},
 "operations": [
  {"id": "pwr-in2", "op": "zone.add", "name": "pwr-corridor", "net": "PWR", "layer": "In2.Cu",
   "polygon": [[4, 8], [36, 8], [36, 12], [4, 12]], "priority": 1, "clearance": 0.3,
   "min_thickness": 0.25, "pad_connection": "solid", "island_removal": "always"},
  {"id": "stitch-1", "op": "via.add", "net": "PWR", "at": [5, 10], "diameter": 0.8, "drill": 0.4,
   "layers": ["F.Cu", "B.Cu"]},
  {"id": "gnd-shrink", "op": "zone.edit", "uuid": "<uuid from ohmai layout zones>",
   "expect": {"net": "GND", "layer": "B.Cu", "polygon": [[1, 1], [39, 1], [39, 19], [1, 19]]},
   "set": {"polygon": [[1, 1], [39, 1], [39, 7], [1, 7]]}},
  {"id": "old-pour", "op": "zone.delete", "uuid": "<uuid>",
   "expect": {"net": "PWR", "layer": "F.Cu", "polygon": [[0, 0], [5, 0], [5, 5], [0, 5]]}}]}
```

Run `ohmai layout apply-changes changes.json --dry-run` first; the RESULTS package shows the
per-net unconnected counts before and after. A stale `expect` (the zone was changed since you
listed it) is refused before anything is written. UUID-only form:
`{"id": "old-pour", "op": "zone.delete", "uuid": "<uuid>", "match": "uuid"}`. Multi-layer zones and zones with holes cannot
be edited or deleted by v1 packages; change them in KiCad.

### Pour neck and width measurement (`ohmai layout pour`)

`ohmai layout pour` refills a scratch copy and measures one net on one layer: pieces, area and
the narrowest neck between the attach points (same-net vias and pads on that layer), with its
location. `--board PCB` measures any board read-only (rules from its same-stem `.kicad_pro`/
`.kicad_dru`, else the project's); `--staged` measures the staged copy. Use one or the other.

- Attach points are only the vias and pads whose copper touches the pour. Others (a via used
  only by tracks on other layers) are listed under `unattached` instead of forcing the neck to 0.
- Attach copper is merged into the pour, and holes around it (via/PTH drill holes, thermal-relief
  gaps) are filled before measuring, so a stitch via no longer reads as a 0 mm neck. Other-net
  holes are kept: they really narrow the copper. `neck_raw` is the old unpatched value and
  `holes_patched` counts filled holes. The neck search is no longer capped at 2 mm.
- `pieces_detail` gives area, attach points and neck per piece (largest first), useful when a
  layer holds several separate pours.
- `--section x=MM` / `y=MM` (repeatable) reports every crossing of that line: `chord` is the raw
  length along the cut, `width` the diameter of the largest disk that fits on the crossing, which
  equals the perpendicular width of a straight run however the cut is angled. A cut that runs
  along an outline edge (for example x=100 on a vertical jog edge) is dropped instead of reading
  as copper. `--width-at X,Y` reports the minimum chord through a point over all directions; take
  points in straight runs, since near an inside corner the shortest chord cuts the corner.

```sh
ohmai layout pour --board hardware/board.kicad_pcb --net PWR --layer In2.Cu --section x=100 --json
```

### Current sizing (`ohmai layout current`)

A screening calculator for how much copper a current path needs. It answers, per target current:
the cross-section required, the width needed on each layer alone, the width of a corridor that
uses the same width on every listed layer, and (when widths are given) the capacity, temperature
rise, resistance, voltage drop and dissipation, plus stitching-via current and count.

```sh
ohmai layout current --layers F.Cu,B.Cu,In2.Cu \
  --target continuous=15 --target combined=19.5 --target peak=20@30 \
  --width F.Cu=8 --width B.Cu=8 --width In2.Cu=8 --length 40 \
  --via-drill 0.4 --via-count 10
ohmai layout current --copper-all 2oz --target 10            # bare widths for 2 oz outer layers
ohmai layout pour --net PWR --layer In2.Cu --json > in2.json
ohmai layout current --layers In2.Cu --pour-json In2.Cu=in2.json --target continuous=15
```

- `--target NAME=AMPS[@RISE]` is repeatable; `RISE` (°C) defaults to `--rise` (20).
- Copper thickness comes from `--copper LAYER=THICK` / `--copper-all` (`1oz`, `35um`, `0.035mm`),
  else from the board stackup (`--board`, default the project board), else 1 oz outer / 0.5 oz inner.
- `--width LAYER=MM` is the narrowest width of that layer along the path; `--pour-json` takes the
  neck reported by `ohmai layout pour --json`. A layer whose pour is several separate pieces has
  neck 0, so `--pour-json` refuses it and lists `pieces_detail` necks; pass the corridor piece's
  neck with `--width`. Widths on different layers count together only if
  their necks overlap in position; the tool cannot know that, so measure honestly.
- Via barrels are π·t·(d−t) with plating `--via-plating` (default 20 µm; ask your fab) and are sized
  as internal conductors. "Per layer change" means every place the current moves between layers.
- Exit 0 when every measured check passes (or nothing was measured), 1 on any FAIL, 2 on bad input.
  `--json` gives the full machine-readable report.

Method (`--method ipc2152`, default): the curve fit of the IPC-2152 generic (external, no modifiers)
chart, A[mil²] = (117.555·ΔT^-0.913 + 1.15)·I^(0.84·ΔT^-0.108 + 1.159), which gives 513 mil² for
10 A at +20 °C against about 500 mil² read off the chart. No board-thickness, plane or copper-weight
modifiers are applied, so results are conservative for boards with planes. `--method ipc2221` uses
the legacy I = k·ΔT^0.44·A^0.725 (k = 0.048 external, 0.024 internal). Resistance uses copper
ρ = 1.72e-8 Ω·m at 20 °C, α = 0.00393/K, at ambient plus the computed rise. This is a sizing
aid, not a thermal simulation: hot components, enclosures and adjacent heat sources are ignored.

### Shared deterministic native refill

All refill paths use `layout/refill.py`: guard (including handroute and apply-changes),
and pour measurement. `layout pour` now measures a freshly refilled scratch copy rather than
trusting cached polygons; its JSON names `measured_board` and durable `refill` evidence.
Neither source PCB nor project/rules are changed by pour measurement.

**Place matching `.kicad_pro` and `.kicad_dru` beside the scratch `.kicad_pcb`, under the same
stem, before `pcbnew.LoadBoard`.** KiCad initializes its DRC engine/custom rules and netclass
caches during load. Do not load a naked PCB and call `BOARD.SetProject` afterwards: switching
an initialized board can crash in native code and leaves the old DRC rule context. Native
apply staging follows the same no-project-switch rule. Save with `skipSettings=True`.

Each refill attempt gets immutable same-stem input/rule copies in a fresh subprocess. The
first uses KiCad's normal all-zone fill; a SIGSEGV retries in a new process pinned to one
allowed CPU (where supported), then a third clean one-CPU process with explicit netclass/
connectivity rebuilding. These are equivalent all-zone fill paths, not per-zone partial
results. No failed output is reused and no cached fill is accepted as a fallback. Other
errors fail immediately. KiCad 9.0.2 has no standalone CLI fill subcommand; DRC/export must
not be mistaken for a refill. Do not rely on `MaximumThreads` advanced config in headless
9.0.2: its configuration loader returns defaults without a wx application.

`refill-attempts.json` records every mode/return code/stdout/stderr and the successful
canonical filled-polygon SHA-256. Canonicalization ignores generated UUIDs, polygon order, winding and starting vertex,
but preserves zone geometry/net/layers, duplicate-zone multiplicity, island flags and actual
vertices in integer IU.
Keep full PCB/pro/dru, native fault traces and partial files for any failed attempt. A
successful fill never overwrites project settings. Timestamped backups precede overwritten
outputs/evidence. The normal and recovery paths must produce the same canonical polygons;
use synthetic mocked-native tests in the normal suite and real-KiCad repeated comparisons
when investigating native crashes.


### Autorouter signal layers and plane reservations

With zero optional configuration, `layout autoroute` derives its allowed signal
layers from the exported board stackup. DSN power-type layers, board power layers,
inner plane polygons and layers named by `planes: {NET: layer}` are **not** signal
routing layers. Standard names such as `In1.Cu` resolve to custom DSN names such
as `GND`. Every network class gets an explicit `circuit/use_layer`, including
classes with no prior circuit or via rule. Unknown layer names fail preparation.

Non-signal layers retain their original dedicated-plane polygons. An empty
power layer gets a board-boundary plane on a pinless, reserved-name net
(`__ohmai_reserved_layer_<layer>`). These are DSN-only reservations, **not new
physical PCB copper**, and must never be imported as zones. Plane declarations
and their pinless net definitions survive scoped net selection. This makes
Freerouting 2.5.0 honour the layer's power role and still permits through-vias
and antipads. A power type alone is insufficient: Freerouting treats it as
signal when no plane exists. Full-layer `keepout` **and `wire_keepout`** were
rejected by the forced F.Cu/B.Cu via control: both prevent through-via routing. Plane net copper already on
shared signal layers remains fixed/obstructing. Shared signal-layer zone outlines
are deferred to native refill (an exported outline is not a filled polygon).
**Always refill and native-guard an additive SES import**, including every net,
short/crossing, hole clearance, connection width, SAFETY and non-zone clearance.
An SES alone is never a gate pass or a claim that a shared-layer pour survived.

Explicit `signal_layers` overrides inferred roles; `inner_signal_layer` adds that
layer even if it was inferred as reserved. `autoroute.inner_nets` still limits
that optional inner layer to its matching class. Overrides are deliberate board
policy, not an automatic invitation to route through power planes.
Preparation warns if an existing SES contains new non-signal-layer wires. A new
router result with such wires fails (`non_signal_layer_wires`) and records layer
counts: reroute rather than retaining plane-layer stubs or importing blindly.
