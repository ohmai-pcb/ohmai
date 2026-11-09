# Phase 11: Fab outputs and release (Pre-order human gate)

**Goal:** a tagged, reproducible release `rev-A` with a mandatory human pre-order sign-off (`review:fab`).
Run on `main` after phases 7-10 are merged. Check `ohmai check fab --gate`.

## Do
1. `ohmai check fab`: ERC, DRC, BOM lock (with `SINGLE-SOURCE COMPONENTS` highlight), and fab outputs export cleanly
   (preview in `out/fab-preview/`).
2. `ohmai release rev-A --dry-run`: gerbers, drill, PnP/CPL, schematic and assembly PDFs, BOM and a
   MANIFEST with the checklist, written to `out/release/rev-A/`.
3. **Pre-order human sign-off (`review:fab`, `require_kind: human`, `min_independence: 4`)**:
   - Run `ohmai review start fab`.
   - The owner (`kind: human`, who did not author the agent-generated files) inspects and signs off:
     * Gerbers and drill files in a viewer (board outline, copper layers, solder mask, silkscreen, tented/untented vias)
     * CPL / PnP centroids, rotations, and pin-1/polarity marks against the fab's DFM placement preview for every IC, diode, LED, switch, and connector
     * Final locked `bom/bom.csv` and the `SINGLE-SOURCE COMPONENTS` highlight block
     * Fab quote and DFM preview
   - Record the human review in `reviews/fab/<owner>.md` (`kind: human`, `independence: 4`) and verify `ohmai check review:fab` passes.
4. When `ohmai check fab --gate` passes: `ohmai release rev-A --tag` creates the tag locally. **The owner pushes the
   tag** (`git push origin rev-A`). CI (KiBot, a separate process) then builds the GitHub release with
   gerbers, BOM, PnP, iBoM, PDFs and STEP.
5. Generated outputs are never committed. They live only in the release.
6. Record the release in `docs/decisions.md`, together with the fab, order number and date (from the owner).

## Tool issue close-out checkpoint
Complete `docs/tool-issues.md` before release: inventory every used tool and record every bug/limit using the source `docs/templates/tool-issues.md`. `ohmai report tools --output out/tool-issues-draft.md` seeds a local draft from run logs; finish versions, repros/artifacts, impacts, actual workarounds, fix commits, severity and upstream-ready title/body. Explicitly record tools with no observed issues. This report cannot be not-applicable; both the fab phase and release preflight check it. Owner approval is required before filing/sharing anything upstream.

## Gate
`ohmai check review:fab` (`require_kind: human`) passes; the owner ticks the MANIFEST checklist, pushes the tag, and places the order.
