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
- `ohmai layout fanout [--refs U1,...] [--staged]`
- `ohmai layout autoroute [--opt-passes 2] [--timeout 00:10:00]` (hard 3-try router cap on any fixed placement)
- `ohmai layout pour [--staged]`
- `ohmai layout handroute [--spec routes.yaml] [--staged]`
