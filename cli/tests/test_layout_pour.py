# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Pour pieces and necks (ohmai.layout.pourgeom). Needs shapely."""

from __future__ import annotations

import pytest

pytest.importorskip("shapely")

from shapely.geometry import LineString, box  # noqa: E402

from ohmai.layout import pourgeom as P  # noqa: E402

CFG = {"attach": 0.25, "min_neck": 0.5, "local_neck": 0.8}


def pour_with_cut(gap_left: float):
    """10 x 4 mm pour, a vertical track across it at x = 5 leaving gap_left mm of pour at the bottom."""
    outline = box(0, 0, 10, 4)
    trk = LineString([(5, gap_left), (5, 5)]).buffer(0.1)
    return P.model_pour(outline, [(trk, 0.2)], [], box(-1, -1, 11, 5).exterior, 0.2, 0.2)


def test_one_piece_and_neck():
    pour = pour_with_cut(1.5)          # copper below the track end: ~1.5 - 0.3 - 0.2 (edge)
    pts = [(1, 2), (9, 2)]
    n, area = P.piece_count(pour, pts, 0.25)
    assert n == 1 and area > 20
    nk = P.neck(pour, pts, 0.25)
    assert 1.0 < nk < 1.7
    r = P.report(pour, pts, [], CFG)
    assert r["pieces"] == 1 and P.verdict(r, CFG)


def test_split_pour_fails():
    pour = pour_with_cut(-1)           # the track cuts it in two
    r = P.report(pour, [(1, 2), (9, 2)], [], CFG)
    assert r["pieces"] == 2 and r["neck"] == 0.0 and not P.verdict(r, CFG)


def test_narrow_neck_and_baseline():
    pour = pour_with_cut(0.6)          # neck well under 0.5 mm
    r = P.report(pour, [(1, 2), (9, 2)], [(1, 2), (9, 2)], CFG)
    assert r["pieces"] == 1 and r["neck"] < 0.5 and not P.verdict(r, CFG)
    # a board that already had this neck before the change may keep it (no worse than the base)
    assert P.verdict(r, CFG, base={"neck": r["neck"], "neck_local": r["neck_local"]})


def test_section_ignores_cut_along_outline_edge():
    """A cut at x = constant along a vertical outline edge is not copper width (stereo In2 jog)."""
    pour = box(0, 0, 5, 10).union(box(5, 0, 10, 3))   # L shape: vertical edge x=5, y 3..10
    rows = P.section(pour, "x", 5.0)
    assert len(rows) == 1 and rows[0]["end"] == [5.0, 3.0]  # the 3..10 edge run is dropped
    assert abs(rows[0]["width"] - 3.0) < 0.01               # the real width is the 3 mm foot
    assert P.section(pour, "x", 0.0) == []                  # a cut that only runs along an edge
    along = P.section(pour, "x", 2.5)[0]                    # a cut along the 5 mm arm
    assert along["chord"] == 10.0 and abs(along["width"] - 5.0) < 0.01


def test_width_at_is_direction_independent():
    from shapely import affinity
    strip = affinity.rotate(box(0, -1, 20, 1), 30, origin=(0, 0))
    w = P.width_at(strip, *affinity.rotate(LineString([(10, 0.3), (10, 0.3)]), 30, origin=(0, 0)).coords[0])
    assert abs(w - 2.0) < 0.02
    assert P.width_at(strip, 50, 50) is None


def test_wide_neck_is_not_capped_at_two_mm():
    assert P.neck(box(0, 0, 20, 6), [(1, 3), (19, 3)], 0.25) > 5.5


def test_stitch_via_holes_and_detached_vias_do_not_zero_the_neck():
    from shapely.geometry import Point
    via_a, via_b = Point(3, 3).buffer(0.3), Point(17, 3).buffer(0.3)
    # 6 mm corridor; each stitch via sits in a drill hole / thermal gap (a hole in the fill)
    pour = box(0, 0, 20, 6).difference(Point(3, 3).buffer(0.5)).difference(Point(17, 3).buffer(0.5))
    spokes = box(2.2, 2.85, 3.8, 3.15).union(box(16.2, 2.85, 17.8, 3.15))
    pour = pour.union(spokes)
    feats = [dict(kind="via", name="a", xy=(3, 3), shape=via_a), dict(kind="via", name="b", xy=(17, 3), shape=via_b),
             dict(kind="via", name="far", xy=(40, 40), shape=Point(40, 40).buffer(0.3))]
    rep = P.measure(pour, feats, None, CFG)
    assert rep["neck_raw"] < 0.5                 # the old artifact
    assert rep["neck"] > 5.5 and rep["pieces"] == 1 and rep["holes_patched"] == 4  # spokes split each gap ring in two
    assert [u["name"] for u in rep["unattached"]] == ["far"] and rep["attach_points"] == 2
    # Other-net holes are real obstructions and stay.
    blocked = box(0, 0, 20, 6).difference(box(9, -1, 11, 5.5))
    feats2 = [dict(kind="via", name="a", xy=(3, 3), shape=via_a), dict(kind="via", name="b", xy=(17, 3), shape=via_b)]
    assert P.measure(blocked, feats2, None, CFG)["neck"] < 0.6


def test_split_layer_reports_piece_necks():
    pour = box(0, 0, 10, 4).union(box(12, 0, 22, 4))
    pts = [(1, 2), (9, 2), (13, 2), (21, 2)]
    feats = [dict(kind="via", name=str(i), xy=xy, shape=LineString([xy, xy]).buffer(0.2)) for i, xy in enumerate(pts)]
    rep = P.measure(pour, feats, None, CFG)
    assert rep["pieces"] == 2 and rep["neck"] == 0.0
    assert [round(d["neck"]) for d in rep["pieces_detail"]] == [4, 4]


def test_pour_board_flag_and_staged_are_exclusive(tmp_path):
    from ohmai.layout import steps
    with pytest.raises(ValueError, match="either --staged or --board"):
        steps.run_pour({"board": str(tmp_path / "x.kicad_pcb"), "work_dir": str(tmp_path)},
                       staged=tmp_path / "s.kicad_pcb", board_path=tmp_path / "b.kicad_pcb")
    from ohmai.cli import build_parser
    a = build_parser().parse_args(["layout", "pour", "--board", "other.kicad_pcb", "--section", "x=100",
                                   "--width-at", "100,80"])
    assert a.board == "other.kicad_pcb" and a.section == ["x=100"] and a.width_at == ["100,80"]
    with pytest.raises(SystemExit):
        build_parser().parse_args(["layout", "pour", "--board", "a.kicad_pcb", "--staged"])


def test_current_refuses_zero_neck_of_split_pour(tmp_path):
    import json

    from ohmai.layout import current
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"neck": 0.0, "pieces": 3, "pieces_detail": [{"neck": 4.1}, {"neck": None}]}))
    with pytest.raises(ValueError, match="3 separate pieces.*4.1"):
        current.pour_neck(f)


def test_other_net_hole_beside_a_big_pad_is_kept():
    from shapely.geometry import Point
    pad = box(4, 1, 8, 5)
    pour = box(0, 0, 20, 6).difference(Point(9.2, 3).buffer(0.6))   # other-net antipad 0.6 mm from the pad
    feats = [dict(kind="pad", name="Q8.5", xy=(6, 3), shape=pad),
             dict(kind="via", name="b", xy=(17, 3), shape=Point(17, 3).buffer(0.3))]
    assert P.measure(pour, feats, None, CFG)["holes_patched"] == 0
