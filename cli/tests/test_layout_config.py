# SPDX-License-Identifier: Apache-2.0
"""Layout config: defaults, unknown keys, board rules from the .kicad_pro (FICTIONAL project)."""

from __future__ import annotations

import json

import pytest

from ohmai.layout import config as lcfg
from ohmai.project import Project


def proj(tmp_path, layout: str | None, pro: dict | None = None):
    (tmp_path / "hardware").mkdir(exist_ok=True)
    if layout is not None:
        (tmp_path / "hardware" / "layout.yaml").write_text(layout)
    if pro is not None:
        (tmp_path / "hardware" / "t.kicad_pro").write_text(json.dumps(pro))
    return Project(tmp_path, {"hardware": {"pcb": "hardware/t.kicad_pcb"}})


PRO = {"net_settings": {"classes": [{"name": "Default", "clearance": 0.1, "track_width": 0.15, "via_diameter": 0.45,
                                     "via_drill": 0.2},
                                    {"name": "Power", "clearance": 0.15, "track_width": 0.3}],
                        "netclass_patterns": [{"pattern": "^VBUS$", "netclass": "Power"}]},
       "board": {"design_settings": {"rules": {"min_hole_clearance": 0.2, "min_hole_to_hole": 0.25,
                                               "min_copper_edge_clearance": 0.2}}}}


def test_defaults_without_file(tmp_path):
    c = lcfg.load(proj(tmp_path, None))
    assert c["_file"] is None and c["planes"] == {} and c["signal_layers"] == ["F.Cu", "B.Cu"]
    assert c["board"].endswith("hardware/t.kicad_pcb") and c["routes"].endswith("hardware/layout/handroute.json")
    assert c["pour"] is None and c["escape_room"] is None


def test_rules_from_kicad_pro_with_overrides(tmp_path):
    c = lcfg.load(proj(tmp_path, "classes: {Power: {width: 0.25}}\nrules: {pth_hole_trk: 0.28}\n"
                                 "planes: {GND: In1.Cu}\n", PRO))
    r = c["rules_resolved"]
    assert r["classes"]["Power"] == {"clearance": 0.15, "width": 0.25, "via_d": None, "via_h": None}
    assert (r["via_d"], r["via_h"], r["via_hole_cu"], r["pth_hole_trk"], r["hole_hole"], r["edge"]) == \
        (0.45, 0.2, 0.2, 0.28, 0.25, 0.2)
    assert r["patterns"] == [("^VBUS$", "Power")] and r["plane_nets"] == ["GND"]
    # fanout margins derived from the rules when not given
    assert r["fanout"]["clearance"] == 0.17 and r["fanout"]["hole_trk"] == 0.3 and r["fanout"]["edge"] == 0.25


def test_unknown_keys_are_errors(tmp_path):
    with pytest.raises(lcfg.ConfigError, match=r"layout: unknown key\(s\) plane"):
        lcfg.load(proj(tmp_path, "plane: {GND: In1.Cu}\n"))
    with pytest.raises(lcfg.ConfigError, match=r"layout.handroute.inner: unknown key\(s\) costs"):
        lcfg.load(proj(tmp_path, "handroute: {inner: {costs: 2}}\n"))


def test_pour_defaults_and_config_path(tmp_path):
    p = proj(tmp_path, None)
    (tmp_path / "hardware" / "other.yaml").write_text("pour: {net: 3V3, layer: In2.Cu, local: {refs: [U1]}}\n")
    p.config["layout"] = {"config": "hardware/other.yaml"}
    c = lcfg.load(p)
    assert c["pour"]["min_neck"] == 0.5 and c["pour"]["local"] == {"refs": ["U1"], "margin": 2.0,
                                                                   "decoupler_prefix": "C", "decoupler_dist": 2.0}


def test_manufacturing_width_floor_and_explicit_class_minimum(tmp_path):
    import copy

    pro = copy.deepcopy(PRO)
    pro['board']['design_settings']['rules']['min_track_width'] = 0.1
    pro['net_settings']['classes'][1]['min_width'] = 0.18
    c = lcfg.load(proj(tmp_path, None, pro))
    assert c['rules_resolved']['min_track_width'] == 0.127
    assert c['rules_resolved']['classes']['Power']['min_width'] == 0.18
    (tmp_path / 'hardware' / 't.kicad_dru').write_text(
        '(version 1)\n(rule "fictional width floor" (constraint track_width (min 0.2mm)))\n')
    c = lcfg.load(proj(tmp_path, None, pro))
    assert c['rules_resolved']['min_track_width'] == 0.2


def test_safety_custom_edge_setback_applies_to_handroute_and_fanout(tmp_path):
    p = proj(tmp_path, None, PRO)
    (tmp_path / 'hardware' / 't.kicad_dru').write_text(
        '(version 1)\n(rule "fictional SAFETY edge" (constraint edge_clearance (min 0.5mm)))\n')
    rules = lcfg.load(p)['rules_resolved']
    assert rules['edge'] == 0.5
    assert rules['fanout']['edge'] >= 0.5
