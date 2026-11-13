# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""KiCad PCB round-trip through sexp.parse/dumps must remain loadable."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ohmai import sexp
from ohmai.layout import fixture as F


def test_atom_preserves_quotedness_and_str_behavior():
    a = sexp.Atom("F.Cu", quoted=True)
    assert a == "F.Cu"
    assert isinstance(a, str)
    assert a.quoted is True
    b = sexp.Atom("signal", quoted=False)
    assert b == "signal" and b.quoted is False


def test_dumps_requotes_pad_numbers_and_layer_names():
    tree = sexp.parse(
        F.board([F.two_pad("R1", 110.0, 110.0, "SIG", "GND")], vias=[(111.0, 111.0, "GND")])
    )
    text = sexp.dumps(tree)
    assert '(pad "1"' in text
    assert '"F.Cu"' in text
    assert '"GND"' in text  # net name stays quoted


def test_plain_str_layer_names_get_quoted_when_appending():
    """Newly constructed via nodes use plain str; dumps must still quote F.Cu."""
    node = ["via", ["at", "1", "2"], ["size", "0.45"], ["drill", "0.2"],
            ["layers", "F.Cu", "B.Cu"], ["net", "1"]]
    text = sexp.dumps(node)
    assert '(layers "F.Cu" "B.Cu")' in text or '"F.Cu"' in text


@pytest.mark.skipif(not shutil.which("kicad-cli"), reason="kicad-cli not installed")
def test_kicad_loads_fanout_style_roundtrip(tmp_path: Path):
    """Catch the bug Junior hit: fanout wrote a PCB KiCad refused to load."""
    pcb = tmp_path / "tiny.kicad_pcb"
    pcb.write_text(
        F.board(
            [F.two_pad("R1", 110.0, 110.0, "SIG", "GND")],
            vias=[(112.0, 112.0, "GND")],
            edge=(100.0, 100.0, 130.0, 130.0),
        ),
        encoding="utf-8",
    )
    tree = sexp.parse(pcb.read_text(encoding="utf-8"))
    # Same shape _append_copper_to_pcb emits:
    tree.append([
        "via",
        ["at", "111.5", "110.0"],
        ["size", "0.45"],
        ["drill", "0.2"],
        ["layers", "F.Cu", "B.Cu"],
        ["net", "2"],
    ])
    tree.append([
        "segment",
        ["start", "110.48", "110.0"],
        ["end", "111.5", "110.0"],
        ["width", "0.25"],
        ["layer", "F.Cu"],
        ["net", "2"],
    ])
    out = tmp_path / "fanout-rt.kicad_pcb"
    out.write_text(sexp.dumps(tree) + "\n", encoding="utf-8")
    svg = tmp_path / "out.svg"
    r = subprocess.run(
        ["kicad-cli", "pcb", "export", "svg", "-o", str(svg), "--layers", "F.Cu,B.Cu", str(out)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = (r.stdout or "") + (r.stderr or "")
    assert "Failed to load board" not in combined, combined
    assert r.returncode == 0, combined
    assert svg.is_file() and svg.stat().st_size > 0
