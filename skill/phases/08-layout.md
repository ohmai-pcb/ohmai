# Phase 8: Layout

**Goal:** a board that passes DRC with the fab's rules, satisfies every datasheet layout constraint (`critical_loops`,
`keepouts`, `layout_checklist`, escape room, and plane continuity/neck width), and fits the enclosure.
Branch `phase/layout`. Check `ohmai check layout`.

## Bounded region plan and verification
Use skill section 2's systematic fix loop, retro and rationalization table. Before a hard region, write a
mini plan (goal, placement commit before routing, exact checks per step, allowed placement/label/split
escapes). First sufficient rung wins: (1) need it? skip speculative work; (2) reuse OhmAI/project tools
and the capped Freerouting flow; (3) use KiCad/generator primitives; (4) installed allowed subprocess tool;
(5) one clear placement/track edit; (6) only then minimum new code. Never invent maze/BFS/private routers
or generator-local `check()` hill-climbs. Keep the supported Freerouting subprocess, not a private engine.
Reproduce + quote failure → root cause → one written hypothesis → minimal edit → fresh same-command
verification + exit. Never claim progress/completion without fresh evidence. Lazy, not negligent: no
cuts to DRC/ERC, readability, constraints, safety, datasheet calibration or other gates.
Third identical check signature or corrupt attempt history exits 3 (FAIL 1, PASS 0). Preserve and repair
corrupt history before retrying; also obey the fixed-placement 3-routing-attempt cap below.
Stop outranks unbounded redraw: preserve placement and commit `wip:` + scrubbed retro
(signature, three commands/changes/exits, failed hypothesis, proposed OhmAI improvement), then choose a
distinct allowed approach. Owner only for design intent/product trade-offs. No `shortcut:` readability
debt. Review reuse before inventing: unnecessary machinery, existing tools, one placement edit?
End every stop/gate: `Skipped / not checked: …; residual risk: …`.

## Do, in order (4-Stage Incremental Placement + Routing)
1. **Outline & Fab rules**:
   - Put the board outline, mounting holes, and mechanical keep-outs from the enclosure/spec onto `Edge.Cuts`.
   - Put the fab's capabilities (min track/space, drill, annular ring, layer stack) into `hardware/<project>.kicad_dru`
     and `.kicad_pro`, with source URL and date. Encode `docs/safety.md` creepage/clearance rules per net class.
2. **4-Stage Incremental Placement (never place all parts at once before checking)**:
   - **Stage A — Fixed connectors & outline**: Place mechanical/edge connectors (`J*`, mounting holes, switches/LEDs
     with fixed positions) first. Multi-island courtyards (such as dual-row castellation strips `J4`) are modeled per
     disconnected courtyard loop so interior board space stays open. Run `ohmai check drc`.
   - **Stage B — Power stage & hot loops**: Place switching regulators, inductors, power switches, input/output caps,
     and Kelvin sense parts following each datasheet's layout figure. Run sub-second `ohmai check placement` to verify
     `critical_loops:` (pad-to-pad distance `max_mm` and `same_layer: true` for `C_IN -> VIN/PGND`, `L_SW -> SW`,
     HF decouplers) and `keepouts:` (e.g., `no_copper` / `no_tracks` under shielded inductor bodies), then trial-route
     the power hot loops (`ohmai layout handroute --staged`).
   - **Stage C — MCU, flash/crystal & escape fanout**: Place the MCU, flash, crystal, and per-pin decoupling caps.
     Run `ohmai check escape-room` and `ohmai layout fanout --staged` to verify every QFN/BGA pin escapes cleanly to
     planes/inner layers before placing secondary passives.
   - **Stage D — Remaining passives & test points**: Place remaining pull-ups, series resistors, ESD arrays, and test
     points using `ohmai layout fit <REF> --scan [--staged]`.
3. **Autonomous passive package swaps & standard HF decouplers**:
   - You may **autonomously resize standard passives** (`0402`–`0805` resistors/MLCCs and `2016`–`3030` inductors) using
     `ohmai passives swap <REF> --package <PKG>` and **add/co-locate standard datasheet-recommended HF decoupling caps**
     without stopping to ask the owner, provided `ohmai check constraints` passes (effective capacitance at DC bias >=
     datasheet min, voltage/power/current/Isat/DCR ratings, JLCPCB stock & basic-part preference), ERC/DRC/parity stay
     at `0`, and the change is logged in `bom/bom.csv` and `STATUS.md`.
   - Reserve owner prompts strictly for active ICs, connectors, switches, crystals, board outline, or waiving a hard
     `R-xx` / datasheet requirement.
4. **Routing & Hard 3-Try Router Cap**:
   - Route power hot loops, USB/high-speed differential pairs, crystal, and Kelvin sense nets first (`ohmai layout handroute`).
   - Use `ohmai layout autoroute --opt-passes 2` (Freerouting, separate process on a DSN export, capped at 2
     optimization passes by default) for remaining signal nets.
   - **Hard 3-try router cap**: If routing fails to converge (unrouted nets > 0 or DRC errors) **3 times on a given
     placement**, STOP running the router immediately. Diagnose the placement bottleneck with `ohmai check escape-room`
     and `ohmai check placement`, adjust placement or resize blocking passives (`ohmai passives swap`), and re-verify
     `ohmai check placement` before routing again.
   - Use `--staged` on `ohmai layout {fit,fanout,pour,handroute}` to dry-run changes on a temporary `.kicad_pcb` copy
     before overwriting the working board.
5. **Copper pours & plane continuity / neck-width check**:
   - Run `ohmai layout pour` (or verify via `ohmai check layout` / `ohmai check placement`). Every configured power/ground
     pour is checked with `pourgeom` for `pieces == 1` (zero islands) and `min_neck >= threshold`, reporting the exact
     `(x, y)` board coordinates of the narrowest neck.
6. **3D models (`ohmai kicad-libs models`)**:
   - Run `ohmai kicad-libs models` to fetch per-project `.step` models into `out/3dmodels/` and verify 3D body coverage
     before rendering 3D views for the gate.
7. **Pre-review self-audit (`layout_checklist`) & `ohmai check layout --gate`**:
   - Every item in `layout_checklist:` across `parts/constraints/*.yaml` and `hardware/layout.yaml` must be verified
     (`verified: true` with evidence, plus automated `critical_loops` and `keepouts`) before `ohmai review start layout`
     opens the independent review brief.

## Gate (AI review + Owner visual/mechanical sign-off)
Alongside the independent AI review (`review:layout`), require a lightweight **owner visual/mechanical sign-off**
on the 2D and 3D board renders (`docs/layout/routed-2d.png` / `docs/layout/routed-top.png`, `docs/layout/routed-bottom.png`,
and `docs/layout/routed-3d.png`):
- Connector positions, orientation, and cable/plug insertion clearance
- Button and LED ergonomics and visibility
- Keepouts, height restrictions, and mounting / castellation edges
- 1:1 paper print (footprints against real parts) and pin-1/rotation of every IC, diode, and polarised part
Record the owner's visual sign-off in `hardware/layout.yaml` (`layout_checklist`) or `STATUS.md` before locking the layout gate.

## Independent review
Before the gate: `ohmai review start layout` (automatically runs the pre-review `placement` & `layout_checklist`
self-audit), get an independent review at the highest feasible level (skill section 3a), resolve every finding in
`reviews/layout/resolutions.md`; `ohmai check review:layout` must pass.

If this gate's `min_independence` cannot be met, try every better level first and document why each was not
available; only then ask the owner for a waiver (`accepted_independence`, skill section 3a, docs/REVIEWS.md).
