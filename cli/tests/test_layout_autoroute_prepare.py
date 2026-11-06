"""prepare_autoroute uses dsn.patch (string API), not a missing sexp tree."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("shapely")

from ohmai.layout import dsn, steps  # noqa: E402
from ohmai.layout.steps import prepare_autoroute  # noqa: E402

# Minimal Specctra shaped like KiCad 9 (same fixture as test_layout_dsn).
DSN = """(pcb fixture.dsn
  (structure
    (layer F.Cu
      (type signal)
    )
    (layer In1.Cu
      (type signal)
    )
    (layer B.Cu
      (type signal)
    )
    (plane GND (polygon In1.Cu 0  100000 -100000  120000 -100000  120000 -120000  100000 -120000
            100000 -100000))
    (keepout "" (polygon signal 0  110000 -110000  111000 -110000  111000 -111000  110000 -111000))
    (via "Via[0-2]_450:200_um")
  )
  (network
    (net /A
      (pins J1-1 U1-1)
    )
    (net GND
      (pins U1-3)
    )
    (class kicad_default /A
      (circuit
        (use_via "Via[0-2]_450:200_um")
      )
      (rule
        (width 150)
        (clearance 100)
      )
    )
  )
  (wiring
    (wire (path F.Cu 150  103000 -103000  104000 -103000)(net /A)(type route))
    (via "Via[0-2]_450:200_um"  104000 -103000 (net /A)(type route))
  )
)
"""


def test_prepare_autoroute_patches_raw_dsn(tmp_path: Path):
    work = tmp_path / "work"
    work.mkdir()
    raw = work / "board-raw.dsn"
    raw.write_text(DSN, encoding="utf-8")
    board = tmp_path / "hardware" / "board.kicad_pcb"
    board.parent.mkdir(parents=True)
    board.write_text("(kicad_pcb)", encoding="utf-8")
    cfg = {
        "board": str(board),
        "work_dir": str(work),
        "planes": {"GND": "In1.Cu"},
        "signal_layers": ["F.Cu", "B.Cu"],
        "copper": ["F.Cu", "In1.Cu", "B.Cu"],
        "inner_signal_layer": None,
        "autoroute": {
            "passes": 5,
            "opt_passes": 1,
            "timeout": "00:01:00",
            "via_costs": 50,
            "threads": None,
            "inner_nets": None,
            "dsn": {"grow_cutouts": 0.25, "plane_keepout_margin": 0.025, "plane_via_keepout": 0.5},
        },
    }
    res = prepare_autoroute(cfg, dsn_in=raw)
    assert res["dsn_edited"] is True
    patched = Path(res["patched_dsn"])
    assert patched.is_file()
    text = patched.read_text(encoding="utf-8")
    assert "(layer In1.Cu\n      (type power)" in text
    assert "(net /A)(type fix))" in text
    assert "freerouting" in res["jar"] or res["jar"].endswith(".jar")
    assert str(patched) in res["cmd"]
    assert "-de" in res["cmd"]


def test_dsn_module_has_no_fake_tree_api():
    assert hasattr(dsn, "patch")
    assert not hasattr(dsn, "parse")
    assert not hasattr(dsn, "edit_dsn")
    assert not hasattr(dsn, "dumps")


def test_prepare_autoroute_subset_nets(tmp_path: Path):
    work = tmp_path / "work"
    work.mkdir()
    raw = work / "board-raw.dsn"
    raw.write_text(DSN, encoding="utf-8")
    board = tmp_path / "hardware" / "board.kicad_pcb"
    board.parent.mkdir(parents=True)
    board.write_text("(kicad_pcb)", encoding="utf-8")
    cfg = {
        "board": str(board),
        "work_dir": str(work),
        "planes": {},
        "signal_layers": ["F.Cu", "B.Cu"],
        "copper": ["F.Cu", "In1.Cu", "B.Cu"],
        "inner_signal_layer": None,
        "autoroute": {
            "passes": 5, "opt_passes": 1, "timeout": "00:01:00", "via_costs": 50,
            "threads": None, "inner_nets": None,
            "dsn": {"grow_cutouts": 0.0, "plane_keepout_margin": 0.0, "plane_via_keepout": 0.5},
        },
    }
    res = prepare_autoroute(cfg, dsn_in=raw, nets=r"^/A$")
    text = Path(res["patched_dsn"]).read_text(encoding="utf-8")
    assert "(net /A\n" in text
    assert "(net GND\n" not in text
    assert any("subset nets=" in x for x in res["dsn_log"])
