# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""scripts/canary.py: candidate selection and the report; it must never move a pin."""

import importlib.util
from pathlib import Path

import pytest

from ohmai.toolupgrade import Suites

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "canary.py"
spec = importlib.util.spec_from_file_location("canary", SCRIPT)
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)


def test_stable_tags_newest_first():
    pages = {1: [{"name": n} for n in ("10.0.7-rc1", "9.0.9", "v4-backport-5.0.0", "9.0.9.1", "10.0.7", "9.0.10")],
             2: []}
    tags = canary.stable_tags(lambda url: pages.get(int(url.rsplit("=", 1)[1]), []))
    assert tags == ["10.0.7", "9.0.10", "9.0.9.1", "9.0.9"]


def test_pick_libs():
    tags = ["10.0.7", "9.0.10", "9.0.9.1"]
    assert canary.pick_libs("match-app", "9.0.2", tags) == "9.0.10"
    assert canary.pick_libs("latest", "9.0.2", tags) == "10.0.7"
    assert canary.pick_libs("9.0.8", None, []) == "9.0.8"
    with pytest.raises(SystemExit):
        canary.pick_libs("match-app", "8.0.4", tags)


def test_report_shows_breakage_and_no_pin_moves():
    s = Suites([{"case": "erc-vin-not-driven", "check": "erc", "caught": False, "skipped": False, "error": ""}],
               [{"golden": "ldo-led", "status": "DIFF", "diffs": {"symbols": "-Device:R aa\n+Device:R bb\n"}}])
    text = canary.report({"kicad-libs": "9.0.10"}, {"template kicad.libraries": "9.0.9.1"}, s, "BREAKAGE")
    assert "NOT CAUGHT: erc-vin-not-driven" in text and "+Device:R bb" in text and "Pins were not moved" in text


def test_canary_writes_no_pins():
    src = SCRIPT.read_text()
    for forbidden in ("write_lock", "accept(", "_set_libraries", "git commit", "upgrade("):
        assert forbidden not in src, forbidden
