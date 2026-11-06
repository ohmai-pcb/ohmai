"""Netclass-aware handroute/fanout width, clearance inflation, fanout stubs, staged chaining."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ohmai.layout import config as lcfg
from ohmai.layout import fixture as F
from ohmai.layout import steps
from ohmai.project import Project


def _proj(tmp_path: Path, *, classes=None, patterns=None, layout: str = "planes: {GND: B.Cu}\n"):
    hw = tmp_path / "hardware"
    hw.mkdir(exist_ok=True)
    (hw / "layout").mkdir(exist_ok=True)
    pcb = hw / "t.kicad_pcb"
    pcb.write_text(
        F.board(
            [
                F.two_pad("R1", 110.0, 110.0, "VIN", "GND"),
                F.two_pad("R2", 118.0, 110.0, "VIN", "GND"),
                F.two_pad("R3", 110.0, 118.0, "SIG", "GND"),
                F.two_pad("R4", 118.0, 118.0, "SIG", "GND"),
            ],
            edge=(100.0, 100.0, 140.0, 140.0),
        ),
        encoding="utf-8",
    )
    classes = classes or [
        {"name": "Default", "clearance": 0.1, "track_width": 0.2, "via_diameter": 0.45, "via_drill": 0.2},
        {"name": "Power", "clearance": 0.25, "track_width": 0.6, "via_diameter": 0.6, "via_drill": 0.3},
    ]
    patterns = patterns if patterns is not None else [{"pattern": "^VIN$", "netclass": "Power"}]
    (hw / "t.kicad_pro").write_text(
        json.dumps(
            {
                "net_settings": {"classes": classes, "netclass_patterns": patterns},
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
    (hw / "layout.yaml").write_text(layout, encoding="utf-8")
    proj = Project(tmp_path, {"hardware": {"pcb": "hardware/t.kicad_pcb"}})
    return lcfg.load(proj), pcb


def test_class_for_net_resolves_power_width():
    rules = {
        "classes": {
            "Default": {"clearance": 0.1, "width": 0.2, "via_d": 0.45, "via_h": 0.2},
            "Power": {"clearance": 0.25, "width": 0.6, "via_d": 0.6, "via_h": 0.3},
        },
        "patterns": [("^VIN$", "Power")],
        "track_w": 0.2,
        "clearance": 0.1,
        "via_d": 0.45,
        "via_h": 0.2,
    }
    vin = steps._class_for_net(rules, "VIN")
    sig = steps._class_for_net(rules, "SIG")
    assert vin["name"] == "Power" and vin["width"] == 0.6 and vin["via_d"] == 0.6
    assert sig["name"] == "Default" and sig["width"] == 0.2
    assert steps._pair_clearance(rules, "VIN", "SIG") == 0.25


def test_handroute_power_net_uses_netclass_width_not_default(tmp_path: Path):
    cfg, _pcb = _proj(tmp_path)
    hr = steps.run_handroute(cfg, action="route", nets=["VIN", "SIG"], staged=None, dry_run=True)
    assert "VIN" in hr["routed_nets"]
    vin_segs = hr["routes"]["VIN"]
    assert vin_segs
    assert all(abs(s["width"] - 0.6) < 1e-9 for s in vin_segs), vin_segs
    # Default-class SIG stays at Default width (0.2), not Power.
    if "SIG" in hr["routed_nets"]:
        assert all(abs(s["width"] - 0.2) < 1e-9 for s in hr["routes"]["SIG"])


def test_handroute_fallback_when_no_netclass_patterns(tmp_path: Path):
    cfg, _ = _proj(tmp_path, patterns=[])
    hr = steps.run_handroute(cfg, action="route", nets=["VIN"], staged=None, dry_run=True)
    assert "VIN" in hr["routed_nets"]
    assert all(abs(s["width"] - 0.2) < 1e-9 for s in hr["routes"]["VIN"])


def test_fanout_stub_and_netclass_via(tmp_path: Path):
    cfg, _ = _proj(
        tmp_path,
        layout="planes: {GND: B.Cu}\nfanout:\n  escape: [{ref: R1}, {ref: R2}]\n",
    )
    staged = tmp_path / "hardware" / "layout" / "staged.kicad_pcb"
    fo = steps.run_fanout(cfg, staged=staged, dry_run=False)
    assert fo["vias_added"] >= 1
    assert all("pad_x" in v and "pad_y" in v for v in fo["vias"])
    # VIN is Power class — via size/drill/stub width come from netclass, not Default 0.45/0.2.
    vin_vias = [v for v in fo["vias"] if v["net"] == "VIN"]
    assert vin_vias, fo["vias"]
    assert abs(vin_vias[0]["size"] - 0.6) < 1e-9
    assert abs(vin_vias[0]["drill"] - 0.3) < 1e-9
    assert abs(vin_vias[0]["width"] - 0.6) < 1e-9
    text = staged.read_text(encoding="utf-8")
    assert "(via" in text and "(segment" in text
    assert text.count("(segment") >= text.count("(via")


def test_append_midroute_via_without_pad_stub(tmp_path: Path):
    """Layer-change vias have no pad_x/pad_y; they are written and rely on connecting tracks."""
    cfg, pcb = _proj(tmp_path)
    out = tmp_path / "out.kicad_pcb"
    steps._append_copper_to_pcb(
        pcb,
        out,
        [{"net": "GND", "x": 111.0, "y": 111.0, "size": 0.45, "drill": 0.2}],
        [{"net": "GND", "x0": 110.0, "y0": 111.0, "x1": 111.0, "y1": 111.0, "width": 0.2, "layer": "F.Cu"},
         {"net": "GND", "x0": 111.0, "y0": 111.0, "x1": 112.0, "y1": 111.0, "width": 0.2, "layer": "B.Cu"}],
        0.45,
        0.2,
        0.25,
    )
    text = out.read_text(encoding="utf-8")
    assert "(at 111.0 111.0)" in text or "(at 111 111)" in text
    # No fanout stub from a pad — only the two connecting tracks above.
    assert text.count("(segment") >= 2


def test_staged_chaining_preserves_fanout_via(tmp_path: Path):
    cfg, _ = _proj(
        tmp_path,
        layout="planes: {GND: B.Cu}\nfanout:\n  escape: [{ref: R1}, {ref: R2}]\n",
    )
    staged = tmp_path / "hardware" / "layout" / "staged.kicad_pcb"
    fo = steps.run_fanout(cfg, staged=staged, dry_run=False)
    assert fo["vias_added"] >= 1
    n_via_before = staged.read_text(encoding="utf-8").count("(via")
    hr = steps.run_handroute(cfg, action="route", nets=["VIN"], staged=staged, dry_run=False)
    assert "VIN" in hr["routed_nets"]
    text = staged.read_text(encoding="utf-8")
    assert text.count("(via") >= n_via_before
    assert "(width 0.6)" in text or '(width "0.6")' in text or "0.6" in text
    # Width appears on VIN segments
    vin_widths = [s["width"] for s in hr["routes"]["VIN"]]
    assert vin_widths and all(abs(w - 0.6) < 1e-9 for w in vin_widths)


@pytest.mark.skipif(not shutil.which("kicad-cli"), reason="kicad-cli not installed")
def test_netclass_staged_kicad_loadable(tmp_path: Path):
    cfg, _ = _proj(
        tmp_path,
        layout="planes: {GND: B.Cu}\nfanout:\n  escape: [{ref: R1}, {ref: R2}]\n",
    )
    staged = tmp_path / "hardware" / "layout" / "staged.kicad_pcb"
    steps.run_fanout(cfg, staged=staged, dry_run=False)
    steps.run_handroute(cfg, action="route", nets=["VIN"], staged=staged, dry_run=False)
    svg = tmp_path / "out.svg"
    r = subprocess.run(
        ["kicad-cli", "pcb", "export", "svg", "-o", str(svg), "--layers", "F.Cu,B.Cu", str(staged)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert "Failed to load board" not in combined, combined
    assert r.returncode == 0, combined


def test_handroute_layer_change_writes_via(tmp_path: Path):
    """Layer-change points from runs_to_segments are written as vias in the PCB sexp."""
    from ohmai.layout import grid as grid_mod
    from ohmai.layout import fixture as F

    cfg, pcb = _proj(tmp_path)
    # Simulate a path that flips F.Cu -> B.Cu at (112, 110).
    runs = [
        (0, [(110.0, 110.0), (112.0, 110.0)]),
        (1, [(112.0, 110.0), (114.0, 110.0)]),
    ]
    seg_pairs, via_pts = grid_mod.runs_to_segments(runs)
    assert via_pts == [(112.0, 110.0)], via_pts
    layers = ["F.Cu", "B.Cu"]
    tracks = []
    for l0, a, bpt in seg_pairs:
        tracks.append({
            "net": "VIN", "layer": layers[l0],
            "x0": a[0], "y0": a[1], "x1": bpt[0], "y1": bpt[1], "width": 0.6,
        })
    vias = [{"net": "VIN", "x": pt[0], "y": pt[1], "size": 0.6, "drill": 0.3} for pt in via_pts]
    staged = tmp_path / "hardware" / "layout" / "staged.kicad_pcb"
    steps._append_copper_to_pcb(pcb, staged, vias, tracks, 0.45, 0.2, 0.2)
    text = staged.read_text(encoding="utf-8")
    assert "(via" in text
    assert "112.0" in text or "112" in text
    # Via present without requiring a fanout pad stub (mid-route).
    assert text.count("(via") >= 1
    assert text.count("(segment") >= 2


def test_handroute_opposite_layer_pads_emit_via(tmp_path: Path):
    """Pads on opposite copper layers force a via; run_handroute must write it (not discard _vias)."""
    from ohmai.layout import fixture as F
    hw = tmp_path / "hardware"
    hw.mkdir()
    (hw / "layout").mkdir()
    # Build board text then flip R2 pad 1 onto B.Cu only.
    raw = F.board(
        [
            F.two_pad("R1", 110.0, 110.0, "SIG", "GND"),
            F.two_pad("R2", 118.0, 110.0, "SIG", "GND"),
        ],
        edge=(100.0, 100.0, 140.0, 140.0),
    )
    # Fixture emits F.Cu for all pads; move R2's SIG pad (pad "1") to B.Cu.
    # The first pad "1" after footprint R2 is the SIG pad.
    parts = raw.split('(footprint "kb:C_fixture"', 2)
    assert len(parts) == 3
    r2 = parts[2]
    # Replace only the first pad layers block in R2 (pad 1 = SIG).
    r2 = r2.replace(
        '(layers "F.Cu" "F.Mask" "F.Paste")',
        '(layers "B.Cu" "B.Mask" "B.Paste")',
        1,
    )
    pcb_text = parts[0] + '(footprint "kb:C_fixture"' + parts[1] + '(footprint "kb:C_fixture"' + r2
    pcb = hw / "t.kicad_pcb"
    pcb.write_text(pcb_text, encoding="utf-8")
    (hw / "t.kicad_pro").write_text(
        json.dumps({
            "net_settings": {
                "classes": [
                    {"name": "Default", "clearance": 0.1, "track_width": 0.2,
                     "via_diameter": 0.45, "via_drill": 0.2},
                ],
                "netclass_patterns": [],
            },
            "board": {"design_settings": {"rules": {
                "min_hole_clearance": 0.2, "min_hole_to_hole": 0.25,
                "min_copper_edge_clearance": 0.2,
            }}},
        }),
        encoding="utf-8",
    )
    (hw / "layout.yaml").write_text("signal_layers: [F.Cu, B.Cu]\n", encoding="utf-8")
    proj = Project(tmp_path, {"hardware": {"pcb": "hardware/t.kicad_pcb"}})
    cfg = lcfg.load(proj)
    staged = hw / "layout" / "staged.kicad_pcb"
    hr = steps.run_handroute(cfg, action="route", nets=["SIG"], staged=staged, dry_run=False)
    assert "SIG" in hr["routed_nets"], hr
    assert hr.get("vias_added", 0) >= 1, hr
    text = staged.read_text(encoding="utf-8")
    assert "(via" in text
    layers = {s["layer"] for s in hr["routes"]["SIG"]}
    assert layers == {"F.Cu", "B.Cu"} or hr["vias_added"] >= 1


def test_staged_handroute_rerun_does_not_double_segments(tmp_path: Path):
    """Second handroute --staged must wipe-or-replace, not append onto prior handroute copper."""
    cfg, _ = _proj(tmp_path)
    staged = tmp_path / "hardware" / "layout" / "staged.kicad_pcb"
    hr1 = steps.run_handroute(cfg, action="route", nets=["VIN"], staged=staged, dry_run=False)
    assert "VIN" in hr1["routed_nets"]
    n1 = staged.read_text(encoding="utf-8").count("(segment")
    hr2 = steps.run_handroute(cfg, action="route", nets=["VIN"], staged=staged, dry_run=False)
    assert "VIN" in hr2["routed_nets"]
    n2 = staged.read_text(encoding="utf-8").count("(segment")
    assert n2 == n1, f"staged re-run doubled segments: {n1} -> {n2}"
    assert n2 < n1 * 1.5


def test_fanout_via_blocked_by_foreign_epad(tmp_path: Path):
    """Fanout must not place an escape via in/on a foreign EPAD (generator + overlap check)."""
    from ohmai.layout import fixture as F
    hw = tmp_path / "hardware"
    hw.mkdir()
    (hw / "layout").mkdir()
    # QFN at 110,110 with EPAD on GND; VBUS pad of a nearby part whose natural escape
    # ring intersects the EPAD — via cells inside the EPAD must be rejected.
    qnets = {17: "GND"}  # EPAD pin for per_side=4
    for i in range(1, 17):
        qnets[i] = "GND" if i % 2 == 0 else f"IO{i}"
    pcb = hw / "t.kicad_pcb"
    # Place escape source pad almost on top of the QFN EPAD so every short ring hits it.
    pcb.write_text(
        F.board(
            [
                F.qfn("U1", 110.0, 110.0, nets=qnets, ep=2.4),
                F.two_pad("C1", 110.0, 110.0, "VBUS", "GND", size=(0.4, 0.4), pitch=0.5,
                          cy=(-0.6, -0.4, 0.6, 0.4)),
            ],
            edge=(100.0, 100.0, 130.0, 130.0),
        ),
        encoding="utf-8",
    )
    (hw / "t.kicad_pro").write_text(
        json.dumps({
            "net_settings": {
                "classes": [
                    {"name": "Default", "clearance": 0.1, "track_width": 0.2, "via_diameter": 0.45, "via_drill": 0.2},
                ],
                "netclass_patterns": [],
            },
            "board": {"design_settings": {"rules": {
                "min_hole_clearance": 0.2, "min_hole_to_hole": 0.25, "min_copper_edge_clearance": 0.2,
            }}},
        }),
        encoding="utf-8",
    )
    (hw / "layout.yaml").write_text(
        "planes: {GND: B.Cu}\nfanout:\n  escape: [{ref: C1}]\n  steps: [0.05, 0.15, 0.25, 0.4]\n",
        encoding="utf-8",
    )
    proj = Project(tmp_path, {"hardware": {"pcb": "hardware/t.kicad_pcb"}})
    cfg = lcfg.load(proj)
    fo = steps.run_fanout(cfg, staged=tmp_path / "hardware" / "layout" / "staged.kicad_pcb", dry_run=True)
    board = __import__("ohmai.layout.placement", fromlist=["load"]).load(pcb)
    epad = next(p for p in board.pads() if p.ref == "U1" and p.num == "17")
    for v in fo["vias"]:
        if v["net"] != "VBUS":
            continue
        # Via copper must not overlap the foreign EPAD (grown by pad_gap).
        v_box = (v["x"] - v["size"] / 2, v["y"] - v["size"] / 2, v["x"] + v["size"] / 2, v["y"] + v["size"] / 2)
        from ohmai.layout import placement as L
        assert not L.overlap(L.grow(v_box, 0.12), epad.box, 0.0), (
            f"fanout via at ({v['x']},{v['y']}) overlaps U1 EPAD {epad.box}"
        )


def test_staged_project_rules_copied_alongside(tmp_path: Path):
    """Writing staged copper also copies .kicad_pro/.kicad_dru for project-aware DRC."""
    cfg, pcb = _proj(tmp_path)
    # Drop a fake design-rules file next to the live board.
    dru = pcb.with_suffix(".kicad_dru")
    dru.write_text("(version 1)\n", encoding="utf-8")
    staged = tmp_path / "out" / "layout" / "staged.kicad_pcb"
    steps.run_handroute(cfg, action="route", nets=["VIN"], staged=staged, dry_run=False)
    assert staged.is_file()
    assert (staged.parent / pcb.with_suffix(".kicad_pro").name).is_file()
    assert (staged.parent / dru.name).is_file()
