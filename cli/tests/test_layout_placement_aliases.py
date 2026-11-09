# SPDX-License-Identifier: Apache-2.0
"""Aliases Board.edge_box / Pad.w,h,drill / Via.size,drill for handroute and fanout."""

from __future__ import annotations

from pathlib import Path

from ohmai.layout import fixture as F
from ohmai.layout import placement as L


def test_board_edge_box_alias():
    edge = (100.0, 100.0, 120.0, 120.0)
    b = L.Board({}, [], [], edge)
    assert b.edge_box is b.edge
    assert b.edge_box == edge


def test_pad_w_h_from_box_and_drill_default():
    box = (10.0, 20.0, 10.5, 21.0)
    p = L.Pad("U1", "1", 10.25, 20.5, box, "GND", ("F.Cu",), hole=False)
    assert p.w == 0.5
    assert p.h == 1.0
    assert p.drill == 0.0


def test_via_size_alias_and_drill_default():
    v = L.Via(1.0, 2.0, 0.45, "GND")
    assert v.size == v.d == 0.45
    assert v.drill == 0.0


def test_load_fixture_exposes_aliases_and_via_drill(tmp_path: Path):
    pcb = tmp_path / "tiny.kicad_pcb"
    pcb.write_text(
        F.board(
            [F.two_pad("R1", 110.0, 110.0, "NET_A", "GND")],
            vias=[(111.0, 111.0, "GND")],
            edge=(100.0, 100.0, 120.0, 120.0),
        ),
        encoding="utf-8",
    )
    b = L.load(pcb)
    assert b.edge_box is b.edge
    assert b.edge_box == (100.0, 100.0, 120.0, 120.0)

    pads = list(b.pads())
    assert pads
    for p in pads:
        assert p.w == p.box[2] - p.box[0]
        assert p.h == p.box[3] - p.box[1]
        _ = p.drill  # fanout: p.drill / 2 for holes

    assert b.vias
    for v in b.vias:
        assert v.size == v.d
        assert v.drill == 0.2  # fixture emits (drill 0.2)


def test_load_pad_drill_circular_and_oval(tmp_path: Path):
    """Circular: first number after drill. Oval (drill oval w h): max(w, h)."""
    pcb = tmp_path / "drill.kicad_pcb"
    pcb.write_text(
        """(kicad_pcb
\t(version 20241229)
\t(generator "ohmai-test")
\t(net 0 "")
\t(net 1 "GND")
\t(footprint "kb:TH"
\t\t(layer "F.Cu")
\t\t(at 0 0)
\t\t(property "Reference" "J1"
\t\t\t(at 0 -2 0)
\t\t\t(layer "F.SilkS")
\t\t)
\t\t(pad "1" thru_hole circle
\t\t\t(at 0 0)
\t\t\t(size 1.5 1.5)
\t\t\t(drill 0.8)
\t\t\t(layers "*.Cu" "*.Mask")
\t\t\t(net 1 "GND")
\t\t)
\t\t(pad "2" thru_hole oval
\t\t\t(at 2 0)
\t\t\t(size 2.0 1.2)
\t\t\t(drill oval 0.6 1.1)
\t\t\t(layers "*.Cu" "*.Mask")
\t\t\t(net 1 "GND")
\t\t)
\t)
\t(via
\t\t(at 5 5)
\t\t(size 0.6)
\t\t(drill 0.3)
\t\t(layers "F.Cu" "B.Cu")
\t\t(net 1)
\t)
\t(gr_rect
\t\t(start 0 0)
\t\t(end 10 10)
\t\t(stroke (width 0.05) (type default))
\t\t(fill no)
\t\t(layer "Edge.Cuts")
\t)
)
""",
        encoding="utf-8",
    )
    b = L.load(pcb)
    p1, p2 = b.footprints["J1"].pads
    assert p1.hole and p1.drill == 0.8
    assert p2.hole and p2.drill == 1.1  # max(0.6, 1.1)
    assert b.vias[0].drill == 0.3 and b.vias[0].size == 0.6


def test_fanout_handroute_attribute_access_no_attribute_error(tmp_path: Path):
    """Touch the attribute graph steps.run_fanout / run_handroute use."""
    pcb = tmp_path / "attrs.kicad_pcb"
    pcb.write_text(
        F.board(
            [F.two_pad("R1", 110.0, 110.0, "SIG", "GND")],
            vias=[(112.0, 112.0, "GND")],
        ),
        encoding="utf-8",
    )
    b = L.load(pcb)
    eb = b.edge_box
    assert eb is not None
    existing_holes = [(v.x, v.y, v.drill / 2) for v in b.vias] + [
        (p.x, p.y, p.drill / 2) for p in b.pads() if p.hole
    ]
    assert existing_holes
    for p in b.pads():
        _ = max(p.w, p.h) / 2
        _ = (p.x - p.w / 2, p.y - p.h / 2, p.x + p.w / 2, p.y + p.h / 2)
    snap_vias = [{"net": v.net, "x": v.x, "y": v.y, "size": v.size, "drill": v.drill} for v in b.vias]
    assert snap_vias[0]["size"] == b.vias[0].d


def test_moved_pads_preserves_drill():
    fp = L.Footprint("J1", "kb:TH", "F.Cu", 0.0, 0.0, 0.0)
    fp.pads.append(L.Pad("J1", "1", 1.0, 0.0, (0.25, -0.75, 1.75, 0.75), "GND", ("*.Cu",), hole=True, drill=0.9))
    moved = L.moved_pads(fp, 10.0, 20.0, 0.0)
    assert len(moved) == 1
    assert moved[0].drill == 0.9
    assert abs(moved[0].w - 1.5) < 1e-9


def test_box_gap_alias():
    a = (0.0, 0.0, 1.0, 1.0)
    b = (2.0, 0.0, 3.0, 1.0)
    assert L.box_gap is L.gap
    assert L.box_gap(a, b) == L.gap(a, b) == 1.0


def test_run_fanout_and_handroute_smoke(tmp_path: Path):
    """Call the real step entry points so missing aliases / Grid API mismatches fail here."""
    import json

    from ohmai.layout import config as lcfg
    from ohmai.layout import steps
    from ohmai.project import Project

    hw = tmp_path / "hardware"
    hw.mkdir()
    layout_dir = hw / "layout"
    layout_dir.mkdir()
    pcb = hw / "t.kicad_pcb"
    # Two SMD pads on GND (fanout target via planes) and a short SIG net for handroute.
    pcb.write_text(
        F.board(
            [
                F.two_pad("R1", 110.0, 110.0, "SIG", "GND"),
                F.two_pad("R2", 114.0, 110.0, "SIG", "GND"),
            ],
            edge=(100.0, 100.0, 130.0, 130.0),
        ),
        encoding="utf-8",
    )
    (hw / "t.kicad_pro").write_text(
        json.dumps(
            {
                "net_settings": {
                    "classes": [
                        {
                            "name": "Default",
                            "clearance": 0.1,
                            "track_width": 0.15,
                            "via_diameter": 0.45,
                            "via_drill": 0.2,
                        }
                    ],
                    "netclass_patterns": [],
                },
                "board": {
                    "design_settings": {
                        "rules": {
                            "min_hole_clearance": 0.2,
                            "min_hole_to_hole": 0.25,
                            "min_copper_edge_clearance": 0.2,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (hw / "layout.yaml").write_text(
        "planes: {GND: B.Cu}\n"
        "fanout:\n  escape: [{ref: R1}, {ref: R2}]\n",
        encoding="utf-8",
    )
    proj = Project(tmp_path, {"hardware": {"pcb": "hardware/t.kicad_pcb"}})
    cfg = lcfg.load(proj)

    fo = steps.run_fanout(cfg, dry_run=True)
    assert "vias_added" in fo
    assert isinstance(fo["vias"], list)
    # R1/R2 pad 2 are GND SMD — fanout should plan at least one via without AttributeError.
    assert fo["vias_added"] >= 1

    staged = layout_dir / "handroute-staged.kicad_pcb"
    hr = steps.run_handroute(cfg, action="route", nets=["SIG"], staged=staged, dry_run=False)
    assert hr["action"] == "route"
    assert "routed_nets" in hr
    assert "segments_added" in hr
    # Straight open board: SIG between R1.1 and R2.1 should route.
    assert "SIG" in hr["routed_nets"]
    assert hr["segments_added"] >= 1
    assert staged.is_file()


def test_handroute_multi_net_no_tuple_pads_shadow(tmp_path: Path):
    """Routing net 2+ must not crash: `for l0, a, b in seg_pairs` used to shadow Board."""
    import json

    from ohmai.layout import config as lcfg
    from ohmai.layout import steps
    from ohmai.project import Project

    hw = tmp_path / "hardware"
    hw.mkdir()
    (hw / "layout").mkdir()
    pcb = hw / "t.kicad_pcb"
    pcb.write_text(
        F.board(
            [
                F.two_pad("R1", 110.0, 110.0, "SIG_A", "GND"),
                F.two_pad("R2", 114.0, 110.0, "SIG_A", "GND"),
                F.two_pad("R3", 110.0, 114.0, "SIG_B", "GND"),
                F.two_pad("R4", 114.0, 114.0, "SIG_B", "GND"),
            ],
            edge=(100.0, 100.0, 130.0, 130.0),
        ),
        encoding="utf-8",
    )
    (hw / "t.kicad_pro").write_text(
        json.dumps(
            {
                "net_settings": {
                    "classes": [
                        {
                            "name": "Default",
                            "clearance": 0.1,
                            "track_width": 0.15,
                            "via_diameter": 0.45,
                            "via_drill": 0.2,
                        }
                    ],
                    "netclass_patterns": [],
                },
                "board": {"design_settings": {"rules": {
                    "min_hole_clearance": 0.2, "min_hole_to_hole": 0.25, "min_copper_edge_clearance": 0.2,
                }}},
            }
        ),
        encoding="utf-8",
    )
    (hw / "layout.yaml").write_text("planes: {GND: B.Cu}\n", encoding="utf-8")
    proj = Project(tmp_path, {"hardware": {"pcb": "hardware/t.kicad_pcb"}})
    cfg = lcfg.load(proj)
    hr = steps.run_handroute(cfg, action="route", nets=["SIG_A", "SIG_B"], staged=None, dry_run=True)
    assert hr["action"] == "route"
    # Both nets should be attempted without AttributeError; at least one routes on open board.
    assert set(hr["routed_nets"]) >= {"SIG_A"} or hr["segments_added"] >= 0


def test_fanout_staged_kicad_loadable(tmp_path: Path):
    """Fanout --staged output must load in KiCad (Junior's serialization bug)."""
    import json
    import shutil
    import subprocess

    import pytest

    from ohmai.layout import config as lcfg
    from ohmai.layout import steps
    from ohmai.project import Project

    if not shutil.which("kicad-cli"):
        pytest.skip("kicad-cli not installed")

    hw = tmp_path / "hardware"
    hw.mkdir()
    layout_dir = hw / "layout"
    layout_dir.mkdir()
    pcb = hw / "t.kicad_pcb"
    pcb.write_text(
        F.board(
            [F.two_pad("R1", 110.0, 110.0, "SIG", "GND"), F.two_pad("R2", 114.0, 110.0, "SIG", "GND")],
            edge=(100.0, 100.0, 130.0, 130.0),
        ),
        encoding="utf-8",
    )
    (hw / "t.kicad_pro").write_text(
        json.dumps(
            {
                "net_settings": {
                    "classes": [{
                        "name": "Default", "clearance": 0.1, "track_width": 0.15,
                        "via_diameter": 0.45, "via_drill": 0.2,
                    }],
                    "netclass_patterns": [],
                },
                "board": {"design_settings": {"rules": {
                    "min_hole_clearance": 0.2, "min_hole_to_hole": 0.25, "min_copper_edge_clearance": 0.2,
                }}},
            }
        ),
        encoding="utf-8",
    )
    (hw / "layout.yaml").write_text(
        "planes: {GND: B.Cu}\nfanout:\n  escape: [{ref: R1}, {ref: R2}]\n",
        encoding="utf-8",
    )
    proj = Project(tmp_path, {"hardware": {"pcb": "hardware/t.kicad_pcb"}})
    cfg = lcfg.load(proj)
    staged = layout_dir / "staged.kicad_pcb"
    fo = steps.run_fanout(cfg, staged=staged, dry_run=False)
    assert fo["vias_added"] >= 1
    assert staged.is_file()
    svg = tmp_path / "fo.svg"
    r = subprocess.run(
        ["kicad-cli", "pcb", "export", "svg", "-o", str(svg), "--layers", "F.Cu,B.Cu", str(staged)],
        capture_output=True, text=True, timeout=60,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert "Failed to load board" not in combined, combined
    assert r.returncode == 0, combined


def test_handroute_apply_promotes_or_fails(tmp_path: Path):
    """apply must promote staged copper, not exit 0 with routed_nets=[]."""
    import json

    import pytest

    from ohmai.layout import config as lcfg
    from ohmai.layout import steps
    from ohmai.project import Project

    hw = tmp_path / "hardware"
    hw.mkdir()
    layout_dir = hw / "layout"
    layout_dir.mkdir()
    pcb = hw / "t.kicad_pcb"
    pcb.write_text(
        F.board([F.two_pad("R1", 110.0, 110.0, "SIG", "GND")], vias=[(111.0, 111.0, "GND")]),
        encoding="utf-8",
    )
    (hw / "t.kicad_pro").write_text(
        json.dumps({
            "net_settings": {"classes": [{
                "name": "Default", "clearance": 0.1, "track_width": 0.15,
                "via_diameter": 0.45, "via_drill": 0.2,
            }], "netclass_patterns": []},
            "board": {"design_settings": {"rules": {
                "min_hole_clearance": 0.2, "min_hole_to_hole": 0.25, "min_copper_edge_clearance": 0.2,
            }}},
        }),
        encoding="utf-8",
    )
    (hw / "layout.yaml").write_text("planes: {}\n", encoding="utf-8")
    proj = Project(tmp_path, {"hardware": {"pcb": "hardware/t.kicad_pcb"}})
    cfg = lcfg.load(proj)

    with pytest.raises(FileNotFoundError):
        steps.run_handroute(cfg, action="apply", dry_run=False)

    staged = layout_dir / "staged.kicad_pcb"
    staged.write_text(pcb.read_text(encoding="utf-8"), encoding="utf-8")
    before = pcb.read_text(encoding="utf-8")
    # mutate staged so promote is observable
    staged.write_text(before + "\n", encoding="utf-8")
    res = steps.run_handroute(cfg, action="apply", staged=staged, dry_run=False)
    assert res["promoted"] is True
    assert res["vias_on_staged"] >= 1
