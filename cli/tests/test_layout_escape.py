# SPDX-License-Identifier: Apache-2.0
"""Escape-room rules and the free-area scan on FICTIONAL boards (ohmai.layout.fixture); no KiCad needed."""

from __future__ import annotations

import re

from ohmai.layout import escape, placement
from ohmai.layout import fixture as F

RULES = {"via_d": 0.45, "via_h": 0.2, "clearance": 0.1, "track_w": 0.15, "hole_to_hole": 0.25,
         "plane_nets": ["GND", "3V3"]}
QNETS = {**{i: f"/S{i}" for i in range(1, 17)}, 17: "GND"}


def load(tmp_path, fps, vias=(), tracks=(), name="b"):
    p = tmp_path / f"{name}.kicad_pcb"
    p.write_text(F.board(fps, vias, tracks))
    return placement.load(p)


def far_ends():
    """the far end of every QFN signal net (so every pin needs to escape)."""
    out, k = [], 0
    for i in range(1, 17):
        out.append(F.two_pad(f"R{i}", 101.5 + (k % 4) * 1.8, 101.5 + (k // 4) * 1.2, f"/S{i}", "GND"))
        k += 1
    return out


def run(board, cfg, rule):
    findings = escape.Rules(board, {**escape.DEFAULTS, **cfg}, RULES).run()
    return [f for f in findings if f[0] == rule]


def statuses(fs):
    return {f[1] for f in fs}


def test_model_matches_fixture(tmp_path):
    b = load(tmp_path, [F.qfn("U1", 110, 110, QNETS), F.two_pad("C1", 107.15, 110, "/S2", "GND", rot=90)])
    u, c = b.footprints["U1"], b.footprints["C1"]
    assert len(u.pads) == 17 and u.courtyard == (107.9, 107.9, 112.1, 112.1)
    pins, ep = escape.body_pads(u)
    assert ep.num == "17" and len(pins) == 16 and set(escape.sides(u)) == {"L", "R", "T", "B"}
    # rot 90: pad 1 (-0.48, 0) lands below the origin (KiCad: counter-clockwise on screen, y down)
    assert c.pads[0].y > c.y and abs(c.pads[0].box[0] - 106.9) < 1e-9
    assert b.edge == (100, 100, 120, 120)


def test_toe_gap(tmp_path):
    good = load(tmp_path, [F.qfn("U1", 110, 110, QNETS), F.two_pad("C1", 107.15, 110, "/S2", "GND", rot=90)])
    fs = run(good, {"parts": [{"ref": "U1"}]}, "toe-gap")
    assert statuses(fs) == {"PASS"} and any("side L: toe gap 0.80" in f[2] for f in fs)
    bad = load(tmp_path, [F.qfn("U1", 110, 110, QNETS), F.two_pad("C1", 107.6, 110, "/S2", "GND", rot=90)], name="bad")
    fs = run(bad, {"parts": [{"ref": "U1"}]}, "toe-gap")
    bad_fs = [f[2] for f in fs if f[1] == "FAIL"]
    assert len(bad_fs) == 1 and re.fullmatch(r"U1 side L: toe gap 0\.35 mm \(C1\.2~U1\.[12]\) < 0\.65 \(via 0\.45 \+ 2 x 0\.1\)",
                                             bad_fs[0]), bad_fs
    # an exempt part (e.g. a regulator input cap the datasheet puts on the pins) does not count
    assert statuses(run(bad, {"parts": [{"ref": "U1", "exempt": ["C1"]}]}, "toe-gap")) == {"PASS"}


def test_flash_facing(tmp_path):
    qn = {**QNETS, 13: "/Q1", 14: "/Q2", 15: "/Q3", 16: "/Q4"}           # top side pins of the QFN
    # flash above the QFN, columns vertical: the QFN lies off the column ends
    fn = {3: "/Q4", 4: "/Q3", 5: "/Q2", 6: "/Q1"}       # lower pins, left to right = QFN pins 16 ... 13
    for rot, want in ((0, "PASS"), (90, "FAIL")):
        b = load(tmp_path, [F.qfn("U1", 110, 110, qn), F.flash("U2", 110, 105, fn, rot=rot)], name=f"f{rot}")
        fs = run(b, {"flash": [{"ref": "U2", "to": "U1"}]}, "flash-facing")
        assert statuses(fs) == {want}, (rot, fs)
    assert "pin columns run across" in fs[0][2]
    # mirrored pin order (links would cross): FAIL even when the columns face it
    mirrored = {3: "/Q1", 4: "/Q2", 5: "/Q3", 6: "/Q4"}
    b = load(tmp_path, [F.qfn("U1", 110, 110, qn), F.flash("U2", 110, 105, mirrored)], name="fm")
    fs = run(b, {"flash": [{"ref": "U2", "to": "U1"}]}, "flash-facing")
    assert statuses(fs) == {"FAIL"} and "pin order does not match" in fs[0][2]


def test_edge_lane(tmp_path):
    base = [F.qfn("U1", 110, 110, QNETS), F.two_pad("C1", 107.0, 110, "/S2", "GND", rot=90),
            F.strip("J1", 101, 108, ["/E1", "/E2", "/E3", "/E4", "/E5"])]
    good = load(tmp_path, base)
    cfg = {"parts": [{"ref": "U1"}], "edge_parts": ["J1"]}
    assert statuses(run(good, cfg, "edge-lane")) == {"PASS"}
    bad = load(tmp_path, [*base, F.two_pad("R9", 104.0, 110, "/S9", "GND", rot=90)], name="bad")
    fs = run(bad, cfg, "edge-lane")
    assert statuses(fs) == {"FAIL"} and "R9 between C1 and J1." in fs[0][2]
    # the same part off the lane (above the column) is fine
    off = load(tmp_path, [*base, F.two_pad("R9", 104.0, 115, "/S9", "GND", rot=90)], name="off")
    assert statuses(run(off, cfg, "edge-lane")) == {"PASS"}


def test_ring_vias_before_routing_only(tmp_path):
    fps = [F.qfn("U1", 110, 110, QNETS)]
    cfg = {"parts": [{"ref": "U1"}]}
    assert statuses(run(load(tmp_path, fps, vias=[(110, 106.0, "GND")]), cfg, "ring-vias")) == {"PASS"}
    bad = load(tmp_path, fps, vias=[(107.6, 110.2, "GND")], name="bad")
    fs = run(bad, cfg, "ring-vias")
    assert statuses(fs) == {"FAIL"} and "GND via at 107.60,110.20" in fs[0][2]
    # a signal via there is the escape itself; vias under the body (inside the toes) are not in the ring
    assert statuses(run(load(tmp_path, fps, vias=[(107.6, 110.2, "/S3"), (110, 110, "GND")], name="sig"), cfg,
                        "ring-vias")) == {"PASS"}
    routed = load(tmp_path, fps, vias=[(107.6, 110.2, "GND")], tracks=[("/S3", (108, 110), (107, 110), "F.Cu")],
                  name="routed")
    assert statuses(run(routed, cfg, "ring-vias")) == {"INFO"}


def test_inductor_facing(tmp_path):
    qn = {**QNETS, 9: "/SW"}                     # a right-side pin is the switch node
    good = load(tmp_path, [F.qfn("U1", 110, 110, qn), F.two_pad("L1", 114.5, 110.25, "/SW", "/OUT")])
    cfg = {"inductors": [{"ref": "L1", "to": "U1"}]}
    fs = run(good, cfg, "inductor")
    assert statuses(fs) == {"PASS"}, fs
    bad = load(tmp_path, [F.qfn("U1", 110, 110, qn), F.two_pad("L1", 114.5, 110.25, "/OUT", "/SW")], name="bad")
    fs = run(bad, cfg, "inductor")
    assert statuses(fs) == {"FAIL"} and "L1.2 [/SW] is the far pad" in fs[0][2]
    none = load(tmp_path, [F.qfn("U1", 110, 110, QNETS), F.two_pad("L1", 114.5, 110, "/A", "/B")], name="none")
    assert statuses(run(none, cfg, "inductor")) == {"FAIL"}


def test_ring_fill(tmp_path):
    fps = [F.qfn("U1", 110, 110, QNETS)]
    cfg = {"parts": [{"ref": "U1"}]}
    assert statuses(run(load(tmp_path, fps + [F.two_pad("C1", 107.0, 110, rot=90)]), cfg, "ring-fill")) == {"PASS"}
    crowd = []
    for i, (x, y) in enumerate([(x, y) for x in (106.8, 108.8, 110.8, 112.8) for y in (106.9, 113.1)]
                               + [(106.9, y) for y in (108.9, 110.9)] + [(113.1, y) for y in (108.9, 110.9)]):
        crowd.append(F.two_pad(f"C{i + 1}", x, y, cy=(-0.95, -0.95, 0.95, 0.95)))
    fs = run(load(tmp_path, fps + crowd, name="crowd"), cfg, "ring-fill")
    assert statuses(fs) == {"FAIL"} and "> 60%" in fs[0][2]


def test_side_capacity(tmp_path):
    fps = [F.qfn("U1", 110, 110, QNETS), *far_ends()]
    cfg = {"parts": [{"ref": "U1"}]}
    fs = run(load(tmp_path, fps), cfg, "side-capacity")
    assert statuses(fs) == {"PASS"} and len(fs) == 4
    # wall the left side in and leave no room under the body (no EP gap): 4 pins, no lane, no via site
    tight = F.qfn("U1", 110, 110, QNETS, ep=2.0)
    wall = F.two_pad("C1", 107.65, 110, rot=90, pitch=1.1, size=(0.5, 1.6), cy=(-0.95, -0.5, 0.95, 0.5))
    fs = run(load(tmp_path, [tight, wall, *far_ends()], name="tight"), cfg, "side-capacity")
    left = [f for f in fs if "side L" in f[2]][0]
    assert left[1] == "FAIL" and "4 signal pin(s) to escape, capacity 0" in left[2], left
    # plane and skipped nets do not need to escape
    fs = run(load(tmp_path, [tight, wall, *far_ends()], name="tight2"),
             {"parts": [{"ref": "U1", "skip_nets": ["^/S[1-4]$"]}]}, "side-capacity")
    assert [f for f in fs if "side L" in f[2]][0][1] == "PASS"


def test_fits_and_scan(tmp_path):
    b = load(tmp_path, [F.qfn("U1", 110, 110, QNETS), F.two_pad("C1", 107.0, 110, rot=90),
                        F.two_pad("C2", 104.0, 104.0)])
    ok, why = placement.fits(b, "C2", 104.0, 106.0)
    assert ok and not why
    ok, why = placement.fits(b, "C2", 106.5, 110.5)
    assert not ok and "courtyard overlaps C1 by 0.95 x 0.95 mm" in why
    ok, why = placement.fits(b, "C2", 100.5, 104.0, edge_margin=0.3)
    assert not ok and "outside the board" in why[0]
    # turned 90 degrees it fits where it does not fit flat (between the QFN courtyard and the board edge)
    assert not placement.fits(b, "C2", 112.65, 110.0, 0)[0] and placement.fits(b, "C2", 112.65, 110.0, 90)[0]
    spots = placement.scan(b, "C2", (107.0, 110.0), radius=2.0, step=0.1, rots=(0, 90))
    assert spots and all(placement.fits(b, "C2", x, y, r)[0] for _, x, y, r in spots)
    assert spots[0][0] == min(s[0] for s in spots)
    assert placement.scan(b, "C2", (110.0, 110.0), radius=0.3) == []   # on top of the QFN: nothing nearby


def test_move_keeps_pads_with_the_part(tmp_path):
    b = load(tmp_path, [F.two_pad("C2", 104.0, 104.0)])
    placement.move(b, "C2", 106.0, 105.0, 90)
    fp = b.footprints["C2"]
    assert (fp.x, fp.y, fp.rot) == (106.0, 105.0, 90)
    p1 = fp.pads[0]
    assert abs(p1.x - 106.0) < 1e-9 and abs(p1.y - 105.48) < 1e-9          # (-0.48, 0) turned 90 -> below
    assert fp.courtyard == placement.moved_courtyard(fp, 106.0, 105.0, 90)
    assert abs((fp.courtyard[2] - fp.courtyard[0]) - 1.0) < 1e-9


def test_cli_fit(tmp_path, monkeypatch, capsys):
    from ohmai.cli import main

    (tmp_path / "ohmai.yaml").write_text("project: t\nhardware: {pcb: hardware/t.kicad_pcb}\n")
    (tmp_path / "hardware").mkdir()
    (tmp_path / "hardware" / "t.kicad_pcb").write_text(F.board([F.qfn("U1", 110, 110, QNETS),
                                                                F.two_pad("C1", 107.0, 110, "/S2", "GND", rot=90),
                                                                F.two_pad("C2", 104.0, 104.0)]))
    (tmp_path / "hardware" / "layout.yaml").write_text("planes: {GND: B.Cu}\nescape_room: {parts: [{ref: U1}]}\n")
    monkeypatch.chdir(tmp_path)
    assert main(["layout", "fit", "C2", "--at", "104,106"]) == 0
    assert "fits" in capsys.readouterr().out
    assert main(["layout", "fit", "C2", "--at", "106.5,110.5", "--scan", "--radius", "1.5"]) == 1
    out = capsys.readouterr().out
    assert "overlaps C1" in out and "free:" in out
    # a spot that fits but kills the QFN's toe gap
    assert main(["layout", "fit", "C2", "--at", "110,112.65,0", "--rules"]) == 1
    assert "escape-room after the move: FAIL [toe-gap]" in capsys.readouterr().out


def test_fit_ignore_courtyard_still_counts_pads(tmp_path):
    """reference-board D-029: --ignore J4 hid the castellated pads, and a 'fitting' cap shorted them."""
    b = load(tmp_path, [F.two_pad("J4", 110.0, 110.0, "/GP26", "/GP27"), F.two_pad("C2", 104.0, 104.0, "/A", "GND")])
    j = b.footprints["J4"]
    pad = j.pads[0]
    on_pad = (pad.x, pad.y)
    # on top of J4's pad: plain ignore says it fits (the old blind spot), ignore_courtyard does not
    assert placement.fits(b, "C2", *on_pad, ignore=("J4",))[0]
    ok, why = placement.fits(b, "C2", *on_pad, ignore_courtyard=("J4",))
    assert not ok and any("pad" in w and "J4." in w for w in why) and not any("courtyard" in w for w in why)
    # courtyards overlapping but pad copper clear: fits once J4's courtyard is ignored
    c2 = b.footprints["C2"]
    x = max(q.box[2] for q in j.pads) + 0.12 + (c2.x - min(q.box[0] for q in c2.pads))   # pads 0.12 mm clear
    assert not placement.fits(b, "C2", x, 110.0)[0]
    ok, why = placement.fits(b, "C2", x, 110.0, ignore_courtyard=("J4",))
    assert ok, why
    spots = placement.scan(b, "C2", on_pad, radius=1.5, step=0.1, ignore_courtyard=("J4",))
    assert spots and all(placement.fits(b, "C2", sx, sy, r, ignore_courtyard=("J4",))[0] for _, sx, sy, r in spots)
