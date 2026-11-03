"""Unit tests for the 7 OhmAI workflow and tool improvements."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
import yaml

from ohmai import datasheets as ds
from ohmai import models3d
from ohmai import passives as pv
from ohmai.checks.placement import check_placement
from ohmai.cli import build_parser, main
from ohmai.layout import placement as pl
from ohmai.layout import pourgeom as pg
from ohmai.project import find_project

PCB_MULTI_ISLAND = """(kicad_pcb
  (version 20241229)
  (generator "pcbnew")
  (general (thickness 1.6))
  (layers
    (0 "F.Cu" signal)
    (1 "In1.Cu" signal)
    (2 "In2.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
    (46 "B.CrtYd" user)
    (47 "F.CrtYd" user)
  )
  (net 0 "")
  (net 1 "GND")
  (net 2 "+3V3")
  (net 3 "VIN")
  (gr_rect (start 0 0) (end 40 60) (layer "Edge.Cuts") (width 0.1))
  (footprint "Connector_Module:RPi_Pico_Castellated_2x20"
    (layer "F.Cu")
    (at 20 30 0)
    (property "Reference" "J4")
    (property "Value" "RPi_Pico_Castellated_2x20")
    (fp_rect (start -10.5 -25.0) (end -8.0 25.0) (layer "F.CrtYd") (width 0.05))
    (fp_rect (start 8.0 -25.0) (end 10.5 25.0) (layer "F.CrtYd") (width 0.05))
    (pad "1" smd rect (at -9.5 -24.0) (size 1.6 1.0) (layers "F.Cu" "B.Cu") (net 1 "GND"))
    (pad "40" smd rect (at 9.5 -24.0) (size 1.6 1.0) (layers "F.Cu" "B.Cu") (net 3 "VIN"))
  )
  (footprint "Package_QFN:QFN-16_3x3mm_P0.5mm"
    (layer "F.Cu")
    (at 20 30 0)
    (property "Reference" "U1")
    (property "Value" "TPS63001")
    (fp_rect (start -2.0 -2.0) (end 2.0 2.0) (layer "F.CrtYd") (width 0.05))
    (pad "1" smd rect (at -1.5 0.0) (size 0.8 0.3) (layers "F.Cu") (net 3 "VIN"))
    (pad "2" smd rect (at 1.5 0.0) (size 0.8 0.3) (layers "F.Cu") (net 2 "+3V3"))
    (pad "17" smd rect (at 0.0 0.0) (size 1.6 1.6) (layers "F.Cu") (net 1 "GND"))
  )
  (footprint "Capacitor_SMD:C_0603_1608Metric"
    (layer "F.Cu")
    (at 16.0 30.0 0)
    (property "Reference" "C1")
    (property "Value" "100nF")
    (fp_rect (start -1.0 -0.6) (end 1.0 0.6) (layer "F.CrtYd") (width 0.05))
    (pad "2" smd rect (at -0.75 0.0) (size 0.8 0.8) (layers "F.Cu") (net 1 "GND"))
    (pad "1" smd rect (at 0.75 0.0) (size 0.8 0.8) (layers "F.Cu") (net 3 "VIN"))
  )
  (footprint "Inductor_SMD:L_2520_1008Metric"
    (layer "F.Cu")
    (at 24.0 30.0 0)
    (property "Reference" "L1")
    (property "Value" "1uH")
    (fp_rect (start -1.4 -1.2) (end 1.4 1.2) (layer "F.CrtYd") (width 0.05))
    (pad "1" smd rect (at -1.0 0.0) (size 0.8 1.6) (layers "F.Cu") (net 2 "+3V3"))
    (pad "2" smd rect (at 1.0 0.0) (size 0.8 1.6) (layers "F.Cu") (net 1 "GND"))
  )
  (via (at 10 30) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2))
  (via (at 30 30) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2))
  (zone
    (net 2)
    (net_name "+3V3")
    (layer "In2.Cu")
    (polygon (pts (xy 2 2) (xy 38 2) (xy 38 58) (xy 2 58)))
    (filled_polygon
      (layer "In2.Cu")
      (pts (xy 4 4) (xy 36 4) (xy 36 56) (xy 4 56))
    )
  )
)
"""


@pytest.fixture
def demo_proj(tmp_path, monkeypatch):
    d = tmp_path / "demo"
    assert main(["new", str(d), "--idea", "test board", "--no-git"]) == 0
    monkeypatch.chdir(d)
    pcb = d / "hardware" / "demo.kicad_pcb"
    pcb.write_text(PCB_MULTI_ISLAND, encoding="utf-8")
    bom = d / "bom" / "bom.csv"
    bom.write_text(
        "ref,qty,value,mpn,manufacturer,package,footprint,lcsc,datasheet,second_source,approved_alternates,status,notes\n"
        "C1,1,100nF,CL10B104KB8NNNC,Samsung,0603,Capacitor_SMD:C_0603_1608Metric,C1591,datasheets/C1.pdf,Yageo CC0603KRX7R9BB104,,approved,HF bypass\n"
        "L1,1,1uH,DFE252012F-1R0M,Murata,2520,Inductor_SMD:L_2520_1008Metric,C290000,datasheets/L1.pdf,TDK VLS252012,,approved,buck L\n"
        "U1,1,TPS63001,TPS63001GQV,MPS,QFN-16,Package_QFN:QFN-16_3x3mm_P0.5mm,C500000,datasheets/TPS63001.pdf,,,approved,buck IC\n",
        encoding="utf-8",
    )
    return find_project(d)


def test_improvement1_passives_swap_and_guards(demo_proj):
    # Dry-run does not modify files
    res = pv.swap_passive(demo_proj, "C1", "0402", lcsc="C1525", mpn="CL05B104KO5NNNC", dry_run=True)
    assert res["ok"] is True
    assert res["new_footprint"] == "Capacitor_SMD:C_0402_1005Metric"
    assert "0603" in (demo_proj.root / "bom" / "bom.csv").read_text()

    # Live CLI run updates BOM and .kicad_pcb atomically
    rc = main(["passives", "swap", "C1", "--package", "0402", "--lcsc", "C1525", "--mpn", "CL05B104KO5NNNC"])
    assert rc == 0
    bom_txt = (demo_proj.root / "bom" / "bom.csv").read_text()
    assert "Capacitor_SMD:C_0402_1005Metric" in bom_txt
    assert "C1525" in bom_txt
    pcb_txt = (demo_proj.root / "hardware" / "demo.kicad_pcb").read_text()
    assert '"Capacitor_SMD:C_0402_1005Metric"' in pcb_txt

    # Compact inductor resize across 2016..3030 is also allowed
    rc_l = main(["parts", "swap", "L1", "--package", "2016"])
    assert rc_l == 0
    assert "Inductor_SMD:L_2016_2016Metric" in (demo_proj.root / "hardware" / "demo.kicad_pcb").read_text()

    # Active IC U1 or out-of-range package is rejected
    with pytest.raises(ValueError, match="only passive components"):
        pv.swap_passive(demo_proj, "U1", "0402")
    with pytest.raises(ValueError, match="outside the standard autonomous passive range"):
        pv.swap_passive(demo_proj, "C1", "1210")


def test_improvement2c_multi_island_courtyard(demo_proj):
    board = pl.load_board(demo_proj.root / "hardware" / "demo.kicad_pcb")
    j4 = board.footprints["J4"]
    # J4 has 2 separate courtyard islands (left strip at x=9.5..12.0, right strip at x=28.0..30.5)
    assert len(j4.courtyards) == 2

    # U1 at (20, 30) sits squarely between J4's two rows and must PASS without --ignore-courtyard J4!
    ok_mid, probs_mid = pl.fits(board, "U1", 20.0, 30.0, 0.0)
    assert ok_mid, probs_mid

    # Moving U1 onto J4's left row (x=10.5, y=30) or right row (x=29.5, y=30) must FAIL!
    ok_left, probs_left = pl.fits(board, "U1", 10.5, 30.0, 0.0)
    assert not ok_left and any("J4" in p for p in probs_left)
    ok_right, probs_right = pl.fits(board, "U1", 29.5, 30.0, 0.0)
    assert not ok_right and any("J4" in p for p in probs_right)


def test_improvement2b_3_5_placement_keepouts_checklist_and_pour(demo_proj):
    layout_yaml = demo_proj.root / "hardware" / "layout.yaml"
    layout_yaml.write_text(
        yaml.safe_dump(
            {
                "critical_loops": [
                    {
                        "name": "C1_to_U1_VIN",
                        "from": "C1.1",
                        "to": "U1.1",
                        "max_mm": 2.5,
                        "same_layer": True,
                        "cite": "TPS63001 p.24",
                    }
                ],
                "keepouts": [
                    {
                        "name": "under_L1_inner1",
                        "ref": "L1",
                        "layers": ["In1.Cu"],
                        "mode": "no_copper",
                        "cite": "DFE252012F p.3",
                    }
                ],
                "layout_checklist": [
                    {
                        "id": "LC-01",
                        "rule": "Input decoupling C1 placed on same layer within 2.5 mm of U1 VIN pin",
                        "verified": True,
                        "evidence": "C1.1 -> U1.1 = 1.45 mm on F.Cu",
                        "cite": {"page": 24, "section": "PCB Layout"},
                    }
                ],
                "pour": {
                    "net": "+3V3",
                    "layer": "In2.Cu",
                    "box": [2, 2, 38, 58],
                    "min_neck": 0.25,
                },
            }
        ),
        encoding="utf-8",
    )
    res = check_placement(demo_proj)
    assert res.status == "PASS", res.details

    # Unverified layout_checklist item must FAIL check_placement and block review start layout
    cfg = yaml.safe_load(layout_yaml.read_text())
    cfg["layout_checklist"][0]["verified"] = False
    layout_yaml.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    res_unverified = check_placement(demo_proj)
    assert res_unverified.status == "FAIL"
    assert any("LC-01" in d for d in res_unverified.details)
    with pytest.raises(SystemExit, match="pre-review self-audit failed"):
        from ohmai.review import start
        start(demo_proj, "layout")


def test_improvement3_pourgeom_neck_with_xy():
    from shapely.geometry import Polygon, box

    # Hourglass polygon with a 0.4 mm neck at (5.0, 5.0)
    left = box(0.0, 0.0, 4.8, 10.0)
    right = box(5.2, 0.0, 10.0, 10.0)
    bridge = box(4.5, 4.8, 5.5, 5.2)
    shape = left.union(right).union(bridge)
    assert isinstance(shape, Polygon)
    w, xy = pg.neck_with_xy(shape, [(2.0, 5.0), (8.0, 5.0)], 0.5)
    assert 0.3 <= w <= 0.5
    assert xy is not None
    assert 4.5 <= xy[0] <= 5.5 and 4.5 <= xy[1] <= 5.5


def test_improvement2d_4_layout_cli_staged_and_opt_passes(demo_proj, tmp_path):
    # Verify autoroute parser defaults (--opt-passes=2, --timeout=00:10:00)
    ap = build_parser()
    ns = ap.parse_args(["layout", "autoroute"])
    assert ns.opt_passes == 2
    assert ns.timeout == "00:10:00"

    # Verify `ohmai layout fit --at ... --staged` writes staged copy without touching original
    staged_pcb = tmp_path / "staged_fit.kicad_pcb"
    orig_txt = (demo_proj.root / "hardware" / "demo.kicad_pcb").read_text()
    rc = main(["layout", "fit", "C1", "--at", "16.5,30.0,90", "--staged", str(staged_pcb)])
    assert rc == 0
    assert staged_pcb.is_file()
    assert "(at 16.5 30 90)" in staged_pcb.read_text()
    assert (demo_proj.root / "hardware" / "demo.kicad_pcb").read_text() == orig_txt

    # Verify `ohmai layout pour --staged` checks the filled pour on the staged board
    layout_yaml = demo_proj.root / "hardware" / "layout.yaml"
    layout_yaml.write_text(
        yaml.safe_dump({"pour": {"net": "+3V3", "layer": "In2.Cu", "box": [2, 2, 38, 58], "min_neck": 0.25}}),
        encoding="utf-8",
    )
    staged_pour = tmp_path / "staged_pour.kicad_pcb"
    assert main(["layout", "pour", "--staged", str(staged_pour)]) == 0
    assert staged_pour.is_file()


def test_improvement6_models3d_and_datasheets_manifest(demo_proj, tmp_path):
    # 1. `ohmai kicad-libs models` generates fallback dummy .step models in out/3dmodels/
    res = models3d.sync_project_models(demo_proj, dummy_missing=True)
    assert res["total_flagged_missing"] >= 3
    assert (demo_proj.root / "out" / "3dmodels" / "manifest.json").is_file()

    # 2. `ohmai datasheets manifest`, `check`, and `fetch --verify`
    ds_dir = demo_proj.root / "datasheets"
    ds_dir.mkdir(exist_ok=True)
    pdf_bytes = b"%PDF-1.4\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n"
    (ds_dir / "TPS63001.pdf").write_bytes(pdf_bytes)
    src_pdf = tmp_path / "source_TPS63001.pdf"
    src_pdf.write_bytes(pdf_bytes)

    shutil.copy(
        demo_proj.root / "parts" / "constraints" / "TPS63001.example.yaml",
        demo_proj.root / "parts" / "constraints" / "TPS63001.yaml",
    )
    c_path = demo_proj.root / "parts" / "constraints" / "TPS63001.yaml"
    c_data = yaml.safe_load(c_path.read_text())
    c_data.setdefault("datasheet", {})["file"] = "datasheets/TPS63001.pdf"
    c_data["datasheet"]["url"] = f"file://{src_pdf}"
    c_path.write_text(yaml.safe_dump(c_data, sort_keys=False), encoding="utf-8")

    assert main(["datasheets", "manifest"]) == 0
    mpath = ds.manifest_path(demo_proj)
    assert mpath.is_file()
    assert main(["datasheets", "check"]) == 0

    # Delete the local PDF and re-fetch via `ohmai datasheets fetch --verify`
    (ds_dir / "TPS63001.pdf").unlink()
    assert main(["datasheets", "fetch", "--verify", "--dest", str(ds_dir)]) == 0
    assert (ds_dir / "TPS63001.pdf").is_file()
    assert hashlib.sha256((ds_dir / "TPS63001.pdf").read_bytes()).hexdigest() == hashlib.sha256(pdf_bytes).hexdigest()


@pytest.mark.skipif(shutil.which("git") is None, reason="git missing")
def test_improvement6_7_pre_push_guard_and_auto_installed_hooks(tmp_path: Path):
    """Improvement 6 & 7: `ohmai new` auto-installs commit-msg + pre-push hooks, supports `--anonymous`,
    and `check_pre_push` catches tracked PDFs, `@localhost` emails, and anonymous-mode violations."""
    import subprocess

    from ohmai.newproject import create
    from ohmai.trailers import check_pre_push, hook_status

    proj_dir = tmp_path / "demo-board"
    create(proj_dir, idea="Test board", git=True, anonymous=True)
    assert "anonymous: true" in (proj_dir / "ohmai.yaml").read_text(encoding="utf-8")
    ok, msg = hook_status(proj_dir)
    assert ok is True, msg
    assert "pre-push" in msg

    # Clean repo with non-localhost email passes check_pre_push
    subprocess.run(["git", "-C", str(proj_dir), "config", "user.email", "bot@users.noreply.github.com"], check=True)
    subprocess.run(["git", "-C", str(proj_dir), "config", "user.name", "ohmai-bot"], check=True)
    subprocess.run(
        ["git", "-C", str(proj_dir), "commit", "--amend", "--reset-author", "--no-edit"],
        check=True,
    )
    assert check_pre_push(proj_dir) == []

    # Plant a raw .pdf while manifest.yaml exists + a local path in docs/ -> check_pre_push must flag both
    (proj_dir / "datasheets").mkdir(exist_ok=True)
    (proj_dir / "datasheets" / "manifest.yaml").write_text("datasheets: []\n", encoding="utf-8")
    (proj_dir / "datasheets" / "bad.pdf").write_bytes(b"%PDF-1.4 dummy")
    (proj_dir / "docs" / "notes.md").write_text(
        "Copied from /workspace/secret-project on 2026-10-17 14:32.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(proj_dir), "add", "-f", "datasheets/manifest.yaml", "datasheets/bad.pdf", "docs/notes.md"], check=True)
    subprocess.run(
        ["git", "-C", str(proj_dir), "commit", "-m", "test commit\n\nOhmAI-Author: name=bot; kind=agent; vendor=x; model=m; instructions=ohmai-skill 0.1"],
        check=True,
    )
    errs = check_pre_push(proj_dir)
    assert any("raw .pdf binary blob" in e for e in errs), errs
    assert any("/workspace/secret-project" in e for e in errs), errs
    assert any("2026-10-17 14:32" in e for e in errs), errs
