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
