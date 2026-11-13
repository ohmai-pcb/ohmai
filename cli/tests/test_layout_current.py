# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Current/width sizing and zone listing; synthetic inputs only, no KiCad."""
import json
import math

import pytest

from ohmai.layout import current, zones

STACKUP = '''(kicad_pcb (setup (stackup
  (layer "F.Cu" (type "copper") (thickness 0.07))
  (layer "dielectric 1" (type "prepreg") (thickness 0.2))
  (layer "In1.Cu" (type "copper") (thickness 0.0152))
  (layer "In2.Cu" (type "copper") (thickness 0.0152))
  (layer "B.Cu" (type "copper") (thickness 0.035)))))'''


def test_ipc2152_fit_published_check_point():
    # Fit check point: 10 A at +20 C -> 513.1 mil^2 (generic chart: 500 mil^2, within 3 %).
    a = current.area_mil2(10, 20)
    assert a == pytest.approx(513.1, abs=0.5)
    assert abs(a - 500) / 500 < 0.03


@pytest.mark.parametrize('method', current.METHODS)
@pytest.mark.parametrize('amps,rise', [(1, 10), (15, 20), (20, 30), (3, 45)])
def test_inverse_functions_are_consistent(method, amps, rise):
    area = current.area_mil2(amps, rise, method)
    assert current.capacity(area, rise, method) == pytest.approx(amps, rel=1e-6)
    assert current.rise_for(amps, area, method) == pytest.approx(rise, rel=1e-6)


def test_more_current_or_less_rise_needs_more_copper():
    assert current.area_mil2(20, 20) > current.area_mil2(15, 20) > current.area_mil2(15, 40)
    # Legacy IPC-2221 derates internal copper by half the k factor; IPC-2152 does not.
    assert current.area_mil2(5, 20, 'ipc2221', external=False) > current.area_mil2(5, 20, 'ipc2221')
    assert current.area_mil2(5, 20, 'ipc2152', external=False) == current.area_mil2(5, 20, 'ipc2152')


@pytest.mark.parametrize('text,um', [('1oz', 35), ('0.5oz', 17.5), ('2OZ', 70), ('35um', 35),
                                     ('0.035mm', 35), ('18', 18)])
def test_parse_thickness(text, um):
    assert current.parse_thickness(text) == pytest.approx(um)


@pytest.mark.parametrize('bad', ['', 'thick', '-1oz', '0oz', '1 inch'])
def test_parse_thickness_rejects(bad):
    with pytest.raises(ValueError):
        current.parse_thickness(bad)


def test_board_stackup_copper(tmp_path):
    board = tmp_path / 'fictional.kicad_pcb'
    board.write_text(STACKUP)
    assert current.board_copper(board) == pytest.approx({'F.Cu': 70, 'In1.Cu': 15.2, 'In2.Cu': 15.2, 'B.Cu': 35})
    board.write_text('(kicad_pcb (net 0 ""))')
    assert current.board_copper(board) == {}


def test_targets_and_layer_values():
    assert current.parse_target('peak=20@30', 20) == dict(name='peak', amps=20, rise=30)
    assert current.parse_target('15', 10) == dict(name='15A', amps=15, rise=10)
    with pytest.raises(ValueError):
        current.parse_target('peak=lots', 20)
    assert current.parse_layer_values(['F.Cu=2', 'In2.Cu=3.5'], float) == {'F.Cu': 2, 'In2.Cu': 3.5}
    with pytest.raises(ValueError):
        current.parse_layer_values(['F.Cu'], float)


def test_via_barrel_area():
    # 0.3 mm drill, 25 um wall: pi * 25 * 275 um^2.
    assert current.via_area_mil2(0.3, 25) == pytest.approx(math.pi * 25 * 275 / 25.4 ** 2)
    with pytest.raises(ValueError):
        current.via_area_mil2(0.03, 20)


def corridor(**kw):
    copper = {'F.Cu': 35, 'B.Cu': 35, 'In2.Cu': 17.5}
    targets = [current.parse_target(t, 20) for t in ('continuous=15', 'combined=19.5', 'peak=20@30')]
    return current.report(targets, list(copper), copper, **kw)


def test_corridor_width_shares_current_by_cross_section():
    r = corridor()
    assert r['ok'] is None  # nothing measured to judge
    for t in r['targets']:
        width_um = t['corridor_width_mm'] * 1000
        area = width_um * (35 + 35 + 17.5) / 25.4 ** 2
        assert area == pytest.approx(t['required_area_mil2'], rel=1e-3)
        # Each layer alone would need its own full width; thinner inner copper needs more.
        assert t['width_alone_mm']['In2.Cu'] == pytest.approx(2 * t['width_alone_mm']['F.Cu'], rel=1e-3)
        assert t['width_alone_mm']['F.Cu'] > t['corridor_width_mm']


def test_measured_widths_pass_and_fail_per_target():
    need = {t['name']: t['corridor_width_mm'] for t in corridor()['targets']}
    w = need['continuous'] * 1.01
    r = corridor(widths_mm={'F.Cu': w, 'B.Cu': w, 'In2.Cu': w}, length_mm=40)
    by = {t['name']: t for t in r['targets']}
    assert by['continuous']['ok'] and by['continuous']['rise_at_target_c'] < 20
    assert not by['combined']['ok'] and by['combined']['rise_at_target_c'] > 20
    assert r['ok'] is False
    assert by['continuous']['drop_mv'] == pytest.approx(15 * by['continuous']['resistance_mohm'], abs=0.1)
    big = max(need.values()) * 1.01
    assert corridor(widths_mm={'F.Cu': big, 'B.Cu': big, 'In2.Cu': big})['ok'] is True


def test_partial_layers_and_unknown_width_layer():
    r = corridor(widths_mm={'F.Cu': 30})
    assert r['targets'][0]['area_mil2'] == pytest.approx(30000 / 25.4 * 35 / 25.4, abs=0.1)
    with pytest.raises(ValueError, match='not in --layers'):
        corridor(widths_mm={'In1.Cu': 5})
    with pytest.raises(ValueError, match='no copper thickness'):
        current.report([dict(name='a', amps=1, rise=20)], ['In1.Cu'], {})


def test_via_count_and_current_per_via():
    r = corridor(via=dict(drill=0.4, plating=20, count=8))
    for t in r['targets']:
        assert t['vias_needed'] == math.ceil(t['amps'] / t['amps_per_via'])
        assert t['amps_each_via'] == pytest.approx(t['amps'] / 8, abs=1e-3)
        assert t['vias_ok'] == (8 >= t['vias_needed'])
    assert 'per layer change' in current.format_text(r)


def test_pour_neck_file(tmp_path):
    f = tmp_path / 'pour.json'
    f.write_text(json.dumps(dict(net='X', layer='F.Cu', pieces=1, neck=3.39)))
    assert current.pour_neck(f) == 3.39
    f.write_text('{}')
    with pytest.raises(ValueError):
        current.pour_neck(f)


def test_cli_current_json(tmp_path, capsys):
    from ohmai.layout import cli
    board = tmp_path / 'fictional.kicad_pcb'
    board.write_text(STACKUP)
    import argparse
    parser = argparse.ArgumentParser()
    cli.add_parser(parser.add_subparsers())
    args = parser.parse_args(['layout', 'current', '--board', str(board), '--layers', 'F.Cu,B.Cu',
                              '--target', 'continuous=15', '--width', 'F.Cu=20', '--width', 'B.Cu=20',
                              '--via-drill', '0.4', '--via-count', '10', '--json'])
    code = args.fn(args)
    out = json.loads(capsys.readouterr().out)
    assert out['copper_um'] == {'F.Cu': 70, 'B.Cu': 35}
    assert code == (0 if out['ok'] else 1)
    args = parser.parse_args(['layout', 'current', '--board', str(board), '--copper-all', '2oz',
                              '--target', 'x=1', '--json'])
    assert args.fn(args) == 0
    assert json.loads(capsys.readouterr().out)['copper_um'] == {'F.Cu': 70, 'B.Cu': 70}
    args = parser.parse_args(['layout', 'current', '--board', str(board)])
    assert args.fn(args) == 2


ZONES = '''(kicad_pcb (net 0 "") (net 1 "GND") (net 2 "PWR")
 (zone (net 1) (net_name "GND") (layer "B.Cu") (uuid "11111111-1111-1111-1111-111111111111")
   (priority 2) (name "ground")
   (polygon (pts (xy 0 0) (xy 10 0) (xy 10 5))))
 (zone (net 2) (net_name "PWR") (layers "F.Cu" "B.Cu") (uuid "22222222-2222-2222-2222-222222222222")
   (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1))))
 (zone (net 0) (net_name "") (layer "F.Cu") (uuid "33333333-3333-3333-3333-333333333333")
   (keepout (tracks not_allowed)) (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))))'''


def test_zone_listing_gives_edit_expect(tmp_path):
    board = tmp_path / 'fictional.kicad_pcb'
    board.write_text(ZONES)
    rows = zones.list_zones(board)
    assert [r['net'] for r in rows] == ['GND', 'PWR']  # rule area skipped
    gnd, pwr = rows
    assert gnd['expect'] == dict(net='GND', layer='B.Cu', polygon=[[0, 0], [10, 0], [10, 5]])
    assert gnd['priority'] == 2 and gnd['name'] == 'ground' and gnd['editable']
    assert not pwr['editable'] and 'expect' not in pwr
    assert [r['net'] for r in zones.list_zones(board, 'P.R')] == ['PWR']
