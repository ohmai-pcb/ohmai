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
