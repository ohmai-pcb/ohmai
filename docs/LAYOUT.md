# Layout steps (phase 8)

Project settings live in `hardware/layout.yaml` (or the file named by ohmai.yaml `layout.config`). Board rules
(net classes, via size, hole and edge minimums) come from the board's `.kicad_pro`; the file may override them.
Unknown keys are errors. Coordinates are KiCad board mm. Nothing here has defaults tied to one board: every net
name, reference, layer and area comes from the project file.

## Escape room (`ohmai check escape-room`, `ohmai layout fit`)

Placement rules for fine-pitch QFN/QFP parts, checked on the placed board before routing. They come from a
4-layer board whose autorouter plateaued at 22-75 open connections until the placement around its QFN-60 was
reworked (pico-k D-021); every rule is one of the things that rework had to fix.

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
- `ohmai layout pour [--staged]`
- `ohmai layout handroute route --nets NET1,NET2 [--budget-seconds 60] [--max-total-expand 2500000] [--max-searches 128] [--staged]`

### Autoroute fixed-copper normalization

The DSN patch joins degree-two chains with identical net, width, layer and fixed
state into a single polyline. Vertices, branches and copper geometry are unchanged.
This avoids Freerouting 2.2.4's search-tree NPE while normalizing split fixed wires.
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

Handroute also respects resolved custom footprint safety clearances for both
track and via occupancy. Copper inside a foreign safety envelope is never
reopened as an A* seed merely because an own-net pad or track overlaps it.
