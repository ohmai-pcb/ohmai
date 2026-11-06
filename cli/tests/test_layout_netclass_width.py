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


def test_append_skips_orphan_via_without_pad(tmp_path: Path):
    cfg, pcb = _proj(tmp_path)
    out = tmp_path / "out.kicad_pcb"
    steps._append_copper_to_pcb(
        pcb,
        out,
        [{"net": "GND", "x": 111.0, "y": 111.0}],  # no pad_x/pad_y
        [],
        0.45,
        0.2,
        0.25,
    )
    text = out.read_text(encoding="utf-8")
    # Fixture may already contain vias; ensure we did not add an orphan at 111,111 without a stub from this call.
    # Count vias at 111 — should be zero new orphans: the append must skip.
    assert "(at 111.0 111.0)" not in text and "(at 111 111)" not in text


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
