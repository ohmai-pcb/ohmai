"""Handroute terminals are distinct copper islands, never just the first two pads."""
from pathlib import Path

import numpy as np
import pytest

from ohmai.layout import fixture as F
from ohmai.layout import grid, steps
from ohmai.layout import placement as L


def _pad(ref, x, y, net="SIG", size=(0.6, 0.6)):
    fp = F.two_pad(ref, x, y)
    fp["pads"] = [("1", 0, 0, *size, net)]
    return fp


def _case(tmp_path, pads, *, vias=(), tracks=(), layers=None, margin=1.0):
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text(F.board(pads, vias=vias, tracks=tracks, edge=(100, 100, 120, 120)))
    cfg = {
        "board": str(pcb), "base": str(tmp_path / "base.json"),
        "routes": str(tmp_path / "routes.json"), "grid": {"res": 0.1},
        "signal_layers": layers or ["F.Cu", "B.Cu"],
        "handroute": {"search_margin": margin, "max_expand": 100_000},
        "rules_resolved": {"track_w": 0.2, "clearance": 0.1, "via_d": 0.45,
                           "via_h": 0.2, "hole_hole": 0.25},
    }
    return cfg, pcb


def test_three_pads_routes_third_when_first_pair_separated_by_wall(tmp_path):
    cfg, _ = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105),
                              _pad("C", 105, 113)],
                   tracks=[("OTHER", (110, 100), (110, 120), "F.Cu")], layers=["F.Cu"])
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    assert rep["routed_nets"] == ["SIG"]
    segs = rep["routes"]["SIG"]
    assert segs and max(max(s["y0"], s["y1"]) for s in segs) > 112
    assert all(max(s["x0"], s["x1"]) < 110 for s in segs)
    # The blocked pad remains disconnected: routing progress is not completion.
    assert rep["remaining_islands"]["SIG"] == 2


def test_same_net_via_and_track_extend_start_on_both_layers(tmp_path, monkeypatch):
    cfg, _ = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105)],
                   vias=[(114, 105, "SIG")],
                   tracks=[("SIG", (103, 105), (114, 105), "F.Cu")])
    real = grid.astar
    seen = []

    def spy(free, viaok, starts, goals, *args, **kwargs):
        # Locate via cells from the entire net bbox instead of assuming pad centres.
        items = steps._handroute_copper_items(L.load(Path(cfg["board"])), "SIG", cfg["signal_layers"])
        bounds = [s.bounds for _, shapes in items for s in shapes.values()]
        g = grid.Grid.around((min(b[0] for b in bounds) - 1, min(b[1] for b in bounds) - 1,
                              max(b[2] for b in bounds) + 1, max(b[3] for b in bounds) + 1), 0.1)
        via_cells = {(li, y, x) for li, y, x in starts
                     if abs(g.xy(x, y)[0] - 114) < 0.2 and abs(g.xy(x, y)[1] - 105) < 0.2}
        assert {c[0] for c in via_cells} == {0, 1}
        assert all(free[li][y, x] for li, y, x in starts | goals)
        seen.append((starts, goals))
        return real(free, viaok, starts, goals, *args, **kwargs)

    monkeypatch.setattr(grid, "astar", spy)
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    assert seen and rep["routed_nets"] == ["SIG"]
    assert rep["remaining_islands"]["SIG"] == 1
    assert min(min(s["x0"], s["x1"]) for s in rep["routes"]["SIG"]) > 113


def test_connected_first_pair_does_not_hide_third_pad(tmp_path):
    cfg, pcb = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 106, 105), _pad("C", 117, 105)],
                     tracks=[("SIG", (103, 105), (106, 105), "F.Cu")])
    staged = tmp_path / "staged.kicad_pcb"
    rep = steps.run_handroute(cfg, "route", ["SIG"], staged=staged)
    assert rep["routed_nets"] == ["SIG"]
    assert rep["remaining_islands"]["SIG"] == 1
    items = steps._handroute_copper_items(L.load(staged), "SIG", cfg["signal_layers"])
    assert len(steps._handroute_islands(items)) == 1
    assert len(L.load(pcb).segments) == 1  # live board unchanged


def test_already_connected_all_copper_skips_astar(tmp_path, monkeypatch):
    cfg, _ = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105)],
                   vias=[(114, 105, "SIG")], tracks=[("SIG", (103, 105), (117, 105), "F.Cu")])
    def unexpected(*args, **kwargs):
        pytest.fail("already connected net must not call A*")
    monkeypatch.setattr(grid, "astar", unexpected)
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    assert rep["routed_nets"] == []
    assert rep["remaining_islands"]["SIG"] == 1


def test_three_open_islands_all_connect(tmp_path):
    cfg, _ = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105), _pad("C", 111, 113)])
    staged = tmp_path / "staged.kicad_pcb"
    rep = steps.run_handroute(cfg, "route", ["SIG"], staged=staged)
    assert rep["remaining_islands"]["SIG"] == 1
    items = steps._handroute_copper_items(L.load(staged), "SIG", cfg["signal_layers"])
    assert len(steps._handroute_islands(items)) == 1


def test_foreign_clearance_not_unblocked_for_own_terminals(tmp_path):
    cfg, _ = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105),
                              _pad("FOREIGN", 103, 105, "OTHER", size=(2, 2))], layers=["F.Cu"])
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    assert rep["routed_nets"] == []
    assert rep["remaining_islands"]["SIG"] == 2


def test_bare_segment_endpoint_and_unattached_via_are_terminals(tmp_path):
    cfg, _ = _case(tmp_path, [_pad("A", 103, 105)], vias=[(117, 113, "SIG")],
                   tracks=[("SIG", (103, 105), (114, 105), "F.Cu")])
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    assert rep["routed_nets"] == ["SIG"]
    assert rep["remaining_islands"]["SIG"] == 1
    assert min(min(s["x0"], s["x1"]) for s in rep["routes"]["SIG"]) > 113


def test_terminal_raster_does_not_make_cross_layer_islands_touch(tmp_path):
    cfg, pcb = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105)])
    board = L.load(pcb)
    board.footprints["B"].pads[0].layers = ("B.Cu",)
    items = steps._handroute_copper_items(board, "SIG", cfg["signal_layers"])
    islands = steps._handroute_islands(items)
    g = grid.Grid.around((100, 100, 120, 120), 0.1)
    free = {li: np.ones((g.ny, g.nx), bool) for li in (0, 1)}
    terminals = [steps._handroute_terminal_cells(items, island, g, free) for island in islands]
    assert {p[0] for p in terminals[0]} == {0}
    assert {p[0] for p in terminals[1]} == {1}


def test_blocked_first_island_does_not_hide_other_reachable_pair(tmp_path):
    cfg, _ = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105), _pad("C", 117, 113)],
                   tracks=[("OTHER", (110, 100), (110, 120), "F.Cu")], layers=["F.Cu"])
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    assert rep["routed_nets"] == ["SIG"]
    assert rep["remaining_islands"]["SIG"] == 2
    assert all(min(s["x0"], s["x1"]) > 110 for s in rep["routes"]["SIG"])


def test_staged_fanout_is_terminal_but_replaced_route_is_not(tmp_path):
    cfg, pcb = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105)])
    staged = tmp_path / "staged.kicad_pcb"
    steps._append_copper_to_pcb(pcb, staged, [
        {"net": "SIG", "x": 114, "y": 105, "pad_x": 103, "pad_y": 105,
         "size": 0.45, "drill": 0.2, "width": 0.2, "layer": "F.Cu"},
    ], [], 0.45, 0.2, 0.2)
    for _ in range(2):
        rep = steps.run_handroute(cfg, "route", ["SIG"], staged=staged)
        assert rep["routed_nets"] == ["SIG"]
        assert rep["fanout_carried"] == 1
        assert rep["remaining_islands"]["SIG"] == 1
        assert min(min(s["x0"], s["x1"]) for s in rep["routes"]["SIG"]) > 113
        board = L.load(staged)
        assert len(board.vias) == 1
        items = steps._handroute_copper_items(board, "SIG", cfg["signal_layers"])
        assert len(steps._handroute_islands(items)) == 1
    assert len(L.load(pcb).segments) == 0


def test_nonrouting_inner_track_still_connects_island(tmp_path, monkeypatch):
    cfg, _ = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105)],
                   vias=[(103, 105, "SIG"), (117, 105, "SIG")],
                   tracks=[("SIG", (103, 105), (117, 105), "In1.Cu")])
    def unexpected(*args, **kwargs):
        pytest.fail("inner copper already joins the fanout vias")
    monkeypatch.setattr(grid, "astar", unexpected)
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    assert rep["remaining_islands"]["SIG"] == 1
    assert rep["routed_nets"] == []


def test_nonet_epad_not_routed_through(tmp_path):
    """No-net EPAD between two NET_A pads must not take tracks/vias (empty ≠ free space)."""
    from ohmai.layout import placement as L

    epad = {"ref": "Q1", "lib": "kb:QFN", "x": 110, "y": 105, "rot": 0,
            "pads": [("9", 0, 0, 4.0, 4.0, "")], "cy": (-2.2, -2.2, 2.2, 2.2)}
    cfg, pcb = _case(
        tmp_path,
        [_pad("A", 103, 105, "NET_A"), _pad("B", 117, 105, "NET_A"), epad],
        layers=["F.Cu", "B.Cu"],
        margin=2.0,
    )
    board = L.load(pcb)
    ep = next(p for p in board.pads() if p.ref == "Q1" and p.num == "9")
    assert ep.net == ""
    assert steps._same_net("", "NET_A") is False
    assert steps._same_net("", "") is False
    assert steps._same_net(None, "NET_A") is False
    assert steps._same_net("NET_A", "NET_A") is True
    # No-net pad is never a same-net terminal / copper item.
    items = steps._handroute_copper_items(board, "NET_A", cfg["signal_layers"])
    assert len(items) == 2  # pads A and B only

    staged = tmp_path / "staged.kicad_pcb"
    steps.run_handroute(cfg, "route", ["NET_A"], staged=staged)
    st = L.load(staged)
    ep_layers = set(ep.layers)
    for v in st.vias:
        v_box = (v.x - v.size / 2, v.y - v.size / 2, v.x + v.size / 2, v.y + v.size / 2)
        assert not L.overlap(v_box, ep.box, 0.0), f"via shorts no-net EPAD at ({v.x},{v.y})"
    for s in st.segments:
        if s.layer not in ep_layers and "*.Cu" not in ep.layers:
            continue
        hw = s.width / 2
        sb = (min(s.x0, s.x1) - hw, min(s.y0, s.y1) - hw, max(s.x0, s.x1) + hw, max(s.y0, s.y1) + hw)
        assert not L.overlap(sb, ep.box, 0.0), f"staged track shorts no-net EPAD: {s}"


def test_fanout_skips_via_on_nonet_epad(tmp_path):
    """Fanout must not plant via/stub on a no-net EPAD (empty net is foreign)."""
    from ohmai.layout import fixture as F
    from ohmai.layout import placement as L

    q = F.qfn("U1", 110, 110, nets={1: "SIG"}, ep=3.0)  # EPAD pin 17 stays ""
    assert q["pads"][-1][5] == ""
    src = F.two_pad("R1", 110.0, 110.0, "VBUS", "GND", size=(0.4, 0.4), pitch=0.5,
                    cy=(-0.6, -0.4, 0.6, 0.4))
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text(F.board([q, src], edge=(100, 100, 130, 130)))
    cfg = {
        "board": str(pcb),
        "fanout": {"steps": [0.05, 0.15, 0.3], "angles": 24, "escape": [{"ref": "R1"}]},
        "planes": {},
        "rules_resolved": {
            "track_w": 0.2, "clearance": 0.1, "via_d": 0.45, "via_h": 0.2,
            "fanout": {"clearance": 0.12, "hole_trk": 0.2, "hole_hole": 0.25,
                       "edge": 0.2, "pad_gap": 0.12},
            "classes": {"Default": {"clearance": 0.1, "width": 0.2, "via_d": 0.45, "via_h": 0.2}},
        },
        "signal_layers": ["F.Cu", "B.Cu"],
    }
    fo = steps.run_fanout(cfg, dry_run=True)
    epad = next(p for p in L.load(pcb).pads() if p.ref == "U1" and p.num == "17")
    assert epad.net == ""
    for v in fo["vias"]:
        v_box = (v["x"] - v["size"] / 2, v["y"] - v["size"] / 2,
                 v["x"] + v["size"] / 2, v["y"] + v["size"] / 2)
        assert not L.overlap(L.grow(v_box, 0.12), epad.box, 0.0), (
            f"fanout via ({v['x']},{v['y']}) overlaps no-net EPAD {epad.box}"
        )
        stub = (min(v["pad_x"], v["x"]) - v["width"] / 2, min(v["pad_y"], v["y"]) - v["width"] / 2,
                max(v["pad_x"], v["x"]) + v["width"] / 2, max(v["pad_y"], v["y"]) + v["width"] / 2)
        assert not L.overlap(L.grow(stub, 0.12), epad.box, 0.0), (
            f"fanout stub to ({v['x']},{v['y']}) overlaps no-net EPAD"
        )


def test_custom_epad_box_includes_primitives_and_blocks_route(tmp_path):
    """Q5/Q6/Q7 regression: custom EPAD with (size 0.005 0.005) + gr_poly primitive.

    Pad box must come from the primitive polygon, and handroute must not place
    F.Cu track or vias through it.
    """
    epad = {"ref": "Q1", "lib": "kb:DSOP", "x": 110, "y": 105, "rot": 0,
            "pads": [("9", 0, 0, 0.005, 0.005, "")], "cy": (-2.3, -2.3, 2.3, 2.3)}
    cfg, pcb = _case(tmp_path, [_pad("A", 103, 105, "NET_A"), _pad("B", 117, 105, "NET_A"), epad],
                     layers=["F.Cu"], margin=4.0)
    txt = pcb.read_text()
    i = txt.index('(pad "9" smd rect')
    j = txt.index("(size 0.005 0.005)", i) + len("(size 0.005 0.005)")
    prim = ("\n\t\t\t(primitives\n\t\t\t\t(gr_poly\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy -2.125 -1.75) "
            "(xy 2.125 -1.75) (xy 2.125 1.75) (xy -2.125 1.75)\n\t\t\t\t\t)\n"
            "\t\t\t\t\t(width 0.1)\n\t\t\t\t\t(fill yes)\n\t\t\t\t)\n\t\t\t)")
    txt = txt[:i] + txt[i:j].replace("smd rect", "smd custom") + prim + txt[j:]
    pcb.write_text(txt)
    ep = next(p for p in L.load(pcb).pads() if p.ref == "Q1" and p.num == "9")
    assert ep.w > 4.2 and ep.h > 3.5, ep.box
    staged = tmp_path / "staged.kicad_pcb"
    steps.run_handroute(cfg, "route", ["NET_A"], staged=staged)
    st = L.load(staged)
    for v in st.vias:
        vb = (v.x - v.size / 2, v.y - v.size / 2, v.x + v.size / 2, v.y + v.size / 2)
        assert not L.overlap(vb, ep.box, 0.0)
    for s in st.segments:
        if s.layer != "F.Cu":
            continue
        hw = s.width / 2
        sb = (min(s.x0, s.x1) - hw, min(s.y0, s.y1) - hw, max(s.x0, s.x1) + hw, max(s.y0, s.y1) + hw)
        assert not L.overlap(sb, ep.box, 0.0), s


def test_load_exposes_filled_zones_from_stereo_pcb():
    stereo = Path("/workspace/projects/usb-c-12v-stereo/hardware/usb-c-12v-stereo.kicad_pcb")
    if not stereo.is_file():
        pytest.skip("stereo PCB not present")
    board = L.load(stereo)
    assert board.zones, "expected poured zones on stereo board"
    filled = [z for z in board.zones if z.filled and z.net]
    assert any(z.net == "GND" for z in filled)
    assert any(z.net == "VBUS_CD" and z.layer == "F.Cu" for z in filled)


def _inject_zone(pcb: Path, net: str, pts: list[tuple[float, float]], layer: str = "F.Cu", *, filled: bool = True) -> None:
    raw = pcb.read_text().rstrip()
    assert raw.endswith(")")
    xy = " ".join(f"(xy {x} {y})" for x, y in pts)
    fill_node = (
        f'\t\t(filled_polygon (layer "{layer}") (pts {xy}))\n'
        if filled else ""
    )
    zone = f"""
\t(zone
\t\t(net 1)
\t\t(net_name "{net}")
\t\t(layer "{layer}")
\t\t(polygon (pts {xy}))
{fill_node}\t)
"""
    pcb.write_text(raw[:-1] + zone + ")\n")


def test_filled_zone_merges_pad_islands(tmp_path):
    """A same-net pour between two pads is one copper island (no route copper needed)."""
    cfg, pcb = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105)], layers=["F.Cu"])
    _inject_zone(pcb, "SIG", [(102, 104), (118, 104), (118, 106), (102, 106)])
    board = L.load(pcb)
    assert any(z.net == "SIG" and z.filled for z in board.zones)
    items = steps._handroute_copper_items(board, "SIG", ["F.Cu"])
    assert len(steps._handroute_islands(items)) == 1
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    assert rep["remaining_islands"]["SIG"] == 1
    assert "SIG" not in rep.get("routed_nets", [])


def test_foreign_filled_zone_does_not_aabb_block_route(tmp_path):
    """Filled pour AABBs must not wall off free space (stereo GND/power planes)."""
    cfg, pcb = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105)], layers=["F.Cu"], margin=2.0)
    _inject_zone(pcb, "OTHER", [(109, 100), (111, 100), (111, 120), (109, 120)], filled=True)
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    assert rep["routed_nets"] == ["SIG"]
    assert rep["remaining_islands"]["SIG"] == 1


def test_unfilled_keepout_zone_blocks_path(tmp_path):
    cfg, pcb = _case(tmp_path, [_pad("A", 103, 105), _pad("B", 117, 105)], layers=["F.Cu"], margin=2.0)
    _inject_zone(pcb, "", [(109, 100), (111, 100), (111, 120), (109, 120)], filled=False)
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    assert rep["remaining_islands"].get("SIG", 0) >= 2 or "SIG" not in rep.get("routed_nets", [])


def test_tiny_pad_seedable_under_foreign_clearance(tmp_path):
    """Foreign clearance inflation must not wipe terminal cells off a tiny same-net pad."""
    # Fat foreign pad between two SIG pads; left SIG is tiny so inflation covers it.
    tiny = _pad("A", 103, 105, size=(0.2, 0.2))
    far = _pad("B", 117, 105, size=(0.6, 0.6))
    wall = _pad("W", 105.2, 105, net="OTHER", size=(2.0, 2.0))
    cfg, pcb = _case(tmp_path, [tiny, far, wall], layers=["F.Cu"], margin=3.0)
    cfg["rules_resolved"] = {"track_w": 0.6, "clearance": 0.25, "via_d": 0.6, "via_h": 0.3,
                             "hole_hole": 0.25,
                             "classes": {"Default": {"width": 0.6, "clearance": 0.25,
                                                     "via_d": 0.6, "via_h": 0.3}},
                             "patterns": []}
    board = L.load(pcb)
    items = steps._handroute_copper_items(board, "SIG", ["F.Cu"])
    assert len(steps._handroute_islands(items)) == 2
    rep = steps.run_handroute(cfg, "route", ["SIG"], dry_run=True)
    # Seedable terminals under foreign clearance → A* can bridge (remaining 1).
    assert rep["remaining_islands"]["SIG"] == 1
    assert "SIG" in rep.get("routed_nets", [])


def test_handroute_try_widths_ladder():
    hr = {"neck_w": 0.15, "residual_neck": True, "alt_widths": {"VBUS_FILT": [0.35]}}
    nc = {"name": "VBUS_20V", "width": 0.6}
    assert steps._handroute_try_widths(hr, nc, "VBUS_FILT") == [0.6, 0.35, 0.45, 0.3, 0.225, 0.15]
    # residual_neck off + class not in neck_classes → only full + explicit alts
    hr2 = {"neck_w": 0.15, "residual_neck": False, "neck_classes": ["Default"],
           "alt_widths": {"SW_NODE": [0.4]}}
    nc2 = {"name": "SW_NODE", "width": 0.8}
    assert steps._handroute_try_widths(hr2, nc2, "SW2_NODE") == [0.8, 0.4]

