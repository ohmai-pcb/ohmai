"""Grid router core (ohmai.layout.grid): A*, via spacing, simplification, rip bookkeeping. Needs numpy."""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")

from ohmai.layout import grid as G  # noqa: E402

RES = 0.05
PITCH = G.via_pitch_cells(0.2, 0.25, RES)        # 0.45 mm = 9 cells


def field(ny=40, nx=80):
    return {0: np.ones((ny, nx), bool), 1: np.ones((ny, nx), bool)}


def layer_changes(path):
    return [p for a, p in zip(path, path[1:], strict=False) if a[0] != p[0]]


def test_grid_around_snaps_and_covers():
    g = G.Grid.around((100.0, 100.0, 121.0, 151.0), 0.05)
    assert (g.x0, g.y0, g.nx, g.ny) == (100.0, 100.0, 420, 1020)
    assert g.xy(0, 0) == (100.025, 100.025) and g.cell(100.06, 100.11) == (1, 2)


def test_straight_path_on_one_layer():
    free = field()
    path = G.astar(free, np.ones((40, 80), bool), {(0, 20, 5)}, {(0, 20, 60)}, [0, 1], {0: 1, 1: 1}, RES, 1.6, PITCH)
    assert path[0] == (0, 20, 5) and path[-1] == (0, 20, 60) and not layer_changes(path)
    runs = G.simplify(path, lambda ix, iy: (ix * RES, iy * RES))
    assert runs == [(0, [(5 * RES, 20 * RES), (60 * RES, 20 * RES)])]


def test_wall_forces_a_via_pair_spaced_by_hole_to_hole():
    """A wall on layer 0 that is only open on layer 1: the path must dive and come back up. The two vias may not
    sit closer than hole + hole_to_hole, even though they are the same net (KiCad hole_to_hole counts own holes)."""
    free = field()
    free[0][:, 38:42] = False              # 0.2 mm wall on layer 0
    viaok = np.ones((40, 80), bool)
    path = G.astar(free, viaok, {(0, 20, 5)}, {(0, 20, 75)}, [0, 1], {0: 1.0, 1: 3.0}, RES, 0.1, PITCH)
    ch = layer_changes(path)
    assert len(ch) == 2
    d = math.hypot(ch[0][1] - ch[1][1], ch[0][2] - ch[1][2])
    assert d >= PITCH - 1e-9, d
    segs, vias = G.runs_to_segments(G.simplify(path, lambda ix, iy: (ix * RES, iy * RES)))
    assert len(vias) == 2 and G.vias_too_close(vias, 0.45) is None


def test_near_path_via_refuses_a_second_via_too_close():
    # path: layer 0 -> via at (10, 10) -> layer 1, three cells east
    parent = {(0, 10, 10): None, (1, 10, 10): (0, 10, 10), (1, 10, 11): (1, 10, 10), (1, 10, 12): (1, 10, 11),
              (1, 10, 13): (1, 10, 12)}
    assert G.near_path_via(parent, (1, 10, 13), 10, 13, PITCH)
    far = dict(parent)
    prev = (1, 10, 13)
    for x in range(14, 22):
        far[(1, 10, x)] = prev
        prev = (1, 10, x)
    assert not G.near_path_via(far, prev, 10, 21, PITCH)


def test_vias_too_close_pairwise():
    assert G.vias_too_close([(0, 0), (0.5, 0), (0.9, 0.1)], 0.45) == ((0.5, 0), (0.9, 0.1))
    assert G.vias_too_close([(0, 0), (0.46, 0)], 0.45) is None


def test_no_path_and_expansion_cap():
    free = field()
    free[0][:, 38:42] = False
    free[1][:, 38:42] = False
    args = ({(0, 20, 5)}, {(0, 20, 75)}, [0, 1], {0: 1, 1: 1}, RES, 1.6, PITCH)
    assert G.astar(free, np.ones((40, 80), bool), *args) is None
    assert G.astar(field(), np.ones((40, 80), bool), *args, max_expand=10) is None


def test_drop_ripped_keeps_manual_entries():
    routes = [{"net": "/A", "why": "auto"}, {"net": "/A", "why": "manual a_fix"}, {"net": "/B"}, {"net": "/AB"}]
    kept, n = G.drop_ripped(routes, r"^/A$")
    assert n == 1 and kept == routes[1:]
    assert G.rip_names(["/A", "/AB", "/B"], "^/A") == ["/A", "/AB"]


def test_netclass_first_match():
    pats = [("^/?(VBUS|GND)$", "Power"), ("USB", "USB")]
    assert G.netclass(pats, "GND") == "Power" and G.netclass(pats, "/USB_DP") == "USB"
    assert G.netclass(pats, "/GP1") == "Default"


def test_manual_records():
    e = {"fix": [{"net": "/A", "w": 0.15, "runs": [["F.Cu", [[1, 1], [2, 1]]], ["B.Cu", [[2, 1], [2, 3]]]]}]}
    (r,) = G.manual_records(e, ["fix"])
    assert r["why"] == "manual fix" and r["vias"] == [[2, 1]]
    assert [t["layer"] for t in r["tracks"]] == ["F.Cu", "B.Cu"]
    with pytest.raises(KeyError, match="no manual entry 'nope'"):
        G.manual_records(e, ["nope"])


def test_copper_island_track_end_via_is_terminal():
    """Repro of the router start bug: an F-only track ending on an F/In2/B via must yield the via as terminal."""
    geom = pytest.importorskip("shapely.geometry")
    track = geom.LineString([(10, 10), (11, 10)]).buffer(0.075)
    via = geom.Point(11, 10).buffer(0.225)
    items = [("/N", {"F": track}),
             ("/N", {"F": via, "In2": via, "B": via}),
             ("/N", {"F": geom.Point(20, 10).buffer(0.225), "B": geom.Point(20, 10).buffer(0.225)}),
             ("/O", {"F": geom.Point(11, 10.2).buffer(0.225)}),
             ("/N", {"B": geom.Point(11.3, 10).buffer(0.1)})]       # touches the via on B only
    isl = G.copper_island(items, 0)
    assert isl == [0, 1, 4]
    assert {lay for i in isl for lay in items[i][1]} == {"F", "In2", "B"}


def test_copper_island_ignores_ripped_copper():
    from shapely.geometry import LineString, Point

    from ohmai.layout.grid import copper_island
    items = [("N", {"F": LineString([(0, 0), (1, 0)]).buffer(0.075)}),
             ("N", {"F": Point(1, 0).buffer(0.225), "B": Point(1, 0).buffer(0.225)})]
    assert copper_island(items, 0) == [0, 1]
    assert copper_island(items, 0, alive={0}) == [0]
    assert copper_island(items, 1, alive={0}) == []


def test_keep_records_manual_survives():
    from ohmai.layout.grid import keep_records
    man = {"net": "N", "why": "manual esc"}
    auto = {"net": "N", "why": "a -> b"}
    other = {"net": "O", "why": "a -> b"}
    assert keep_records([man, auto, other], {"N"}) == [man, other]
