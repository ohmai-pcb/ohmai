# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""prepare_autoroute uses dsn.patch (string API), not a missing sexp tree."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("shapely")

from ohmai.layout import dsn, steps  # noqa: E402
from ohmai.layout.steps import prepare_autoroute  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_freerouting_userdata(tmp_path, monkeypatch):
    """Keep Freerouting --user_data_path inside the test tree, never ~/.config/freerouting."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


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


def test_freerouting_java_bin_honors_env(tmp_path: Path, monkeypatch):
    java = tmp_path / "java"
    java.write_text("#!/bin/sh\n")
    java.chmod(0o755)
    monkeypatch.setenv("FREEROUTING_JAVA", str(java))
    assert steps._freerouting_java_bin() == str(java.resolve())


def test_run_autoroute_fails_on_npe_empty_ses(tmp_path: Path, monkeypatch):
    jar = tmp_path / "freerouting.jar"
    jar.write_bytes(b"pk")
    java = tmp_path / "java"
    java.write_text("#!/bin/sh\n")
    java.chmod(0o755)
    monkeypatch.setenv("FREEROUTING_JAVA", str(java))
    dsn = tmp_path / "board.dsn"
    dsn.write_text("(pcb x)\n", encoding="utf-8")
    ses = tmp_path / "board.ses"
    # protect-only SES (no type route) — must FAIL
    ses.write_text(
        '(session "x"\n  (wiring\n'
        '    (wire (path B.Cu 1000  0 0  1 1)(net VSYS_BB)(type protect))\n'
        "  )\n)\n",
        encoding="utf-8",
    )

    class FakeProc:
        returncode = 0
        stdout = "2026 ERROR  Error during routing passes\nNullPointerException: to_trace_entries is null\n"
        stderr = ""

    monkeypatch.setattr(steps.subprocess if hasattr(steps, "subprocess") else __import__("subprocess"),
                        "run", lambda *a, **k: FakeProc(), raising=False)
    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda *a, **k: FakeProc())

    prepared = {
        "cmd": [str(java), "-jar", str(jar), "-de", str(dsn), "-do", str(ses)],
        "patched_dsn": str(dsn),
        "raw_dsn": str(dsn),
        "dsn_edited": True,
        "ses_out": str(ses),
        "timeout_sec": 5,
    }
    res = steps.run_autoroute(prepared)
    assert res["ok"] is False
    assert res["returncode"] != 0
    assert "freerouting_npe" in res["fail_reasons"] or "routing_pass_error" in res["fail_reasons"]
    assert "ses_no_route_copper" in res["fail_reasons"]
    assert res["ses_route_wires"] == 0


def test_run_autoroute_counts_real_multiline_network_out(tmp_path, monkeypatch):
    import subprocess
    jar = tmp_path / 'freerouting.jar'
    jar.write_bytes(b'pk')
    dsn_file = tmp_path / 'board.dsn'
    dsn_file.write_text('(pcb x)')
    ses = tmp_path / 'board.ses'
    ses.write_text('''(session fictional (routes (network_out
      (net "Net-(U1-A)"
        (wire (path F.Cu 150 0 0 100 100))
        (via "Via[0-1]_450:200_um" 100 100)
        (wire (path F.Cu 150 0 0 50 50) (type protect))
      ))))''')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, '', ''))
    monkeypatch.setattr(steps, '_freerouting_java_bin', lambda: 'java')
    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/java')
    result = steps.run_autoroute(dict(cmd=['java', '-jar', str(jar)], raw_dsn=str(dsn_file),
                                     ses_out=str(ses), timeout_sec=1))
    assert result['ok']
    assert result['ses_route_wires'] == result['ses_route_vias'] == 1


def _auth_cfg(tmp_path: Path) -> tuple[dict, Path]:
    work = tmp_path / "work"
    work.mkdir()
    raw = work / "board-raw.dsn"
    raw.write_text(DSN, encoding="utf-8")
    board = tmp_path / "hardware" / "board.kicad_pcb"
    board.parent.mkdir(parents=True, exist_ok=True)
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
    return cfg, raw


def test_prepare_autoroute_passes_isolated_user_data_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    cfg, raw = _auth_cfg(tmp_path)
    res = prepare_autoroute(cfg, dsn_in=raw)
    flag = next(a for a in res["cmd"] if str(a).startswith("--user_data_path="))
    path = Path(flag.split("=", 1)[1])
    assert path == tmp_path / "data/ohmai/freerouting/2.5.0/userdata"
    assert path.is_dir()
    assert path.as_posix() == res["user_data_path"]
    assert "/.config/freerouting" not in path.as_posix()
    assert "FREEROUTING__USER_DATA_PATH" not in res["cmd"]


def test_run_autoroute_appends_user_data_path_when_missing(tmp_path: Path, monkeypatch):
    import subprocess
    jar = tmp_path / "freerouting.jar"
    jar.write_bytes(b"pk")
    dsn_file = tmp_path / "board.dsn"
    dsn_file.write_text("(pcb x)")
    ses = tmp_path / "board.ses"
    ses.write_text('(session x (routes (network_out (net A (wire (path F.Cu 150 0 0 1 1))))))')
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    seen = {}

    def capture(cmd, **k):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", capture)
    monkeypatch.setattr(steps, "_freerouting_java_bin", lambda: "java")
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/java")
    steps.run_autoroute(dict(cmd=["java", "-jar", str(jar)], raw_dsn=str(dsn_file),
                             ses_out=str(ses), timeout_sec=1))
    flag = next(a for a in seen["cmd"] if str(a).startswith("--user_data_path="))
    path = Path(flag.split("=", 1)[1])
    assert path == tmp_path / "data/ohmai/freerouting/2.5.0/userdata"
    assert path.is_dir()


def test_freerouting_java_bin_discovers_jdk25_without_env(tmp_path: Path, monkeypatch):
    from ohmai import pins
    monkeypatch.delenv("FREEROUTING_JAVA", raising=False)
    monkeypatch.delenv("OHMAI_FREEROUTING_JAVA", raising=False)
    monkeypatch.delenv("JAVA_HOME", raising=False)
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    java = tmp_path / "jdk25/bin/java"
    java.parent.mkdir(parents=True)
    java.write_text('#!/bin/sh\necho \'openjdk version "25.0.4.1" 2026-08-18\' >&2\n')
    java.chmod(0o755)
    monkeypatch.setattr(pins, "jdk_search_roots", lambda: [tmp_path / "jdk25"])
    assert steps._freerouting_java_bin() == str(java.resolve())

