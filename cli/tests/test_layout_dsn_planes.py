# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Fictional four-layer DSN golden controls; no KiCad dependency."""
import pytest

from ohmai import sexp
from ohmai.layout import dsn, steps
from ohmai.layout import dsn_layers as layers

DSN = '''(pcb fictional
  (parser (string_quote "))
  (structure
    (layer F.Cu (type signal) (property (index 0)))
    (layer GND (type power) (property (index 1)))
    (layer PWR (type power) (property (index 2)))
    (layer B.Cu (type signal) (property (index 3)))
    (boundary (path pcb 0 0 0 10000 0 10000 10000 0 10000 0 0))
    (plane GND (polygon GND 0 100 100 9900 100 9900 9900 100 9900))
    (plane GND (polygon B.Cu 0 100 100 9900 100 9900 9900 100 9900))
    (via "Via[0-3]_450:200_um")
  )
  (network
    (net /A (pins J1-1 U1-1))
    (class kicad_default /A
      (circuit (use_via "Via[0-3]_450:200_um"))
      (rule (width 150)))
    (class WithoutCircuit /B (rule (width 200)))
    (class WithOldRule /C (circuit (use_layer GND PWR)))
  )
  (wiring)
)'''
BOARD = {'F.Cu': ('F.Cu', 'signal'), 'In1.Cu': ('GND', 'power'),
         'In2.Cu': ('PWR', 'power'), 'B.Cu': ('B.Cu', 'signal')}


def nodes(text, name):
    section = 'network' if name == 'use_layer' else 'structure'
    text = text[slice(*layers.section(text, section))]
    return [sexp.parse(text[a:b]) for a, b in layers.scopes(text, name)]


def patched(signals=None, planes=None, inner=None):
    return dsn.patch(DSN, {}, planes or {}, [], signals, inner_layer=inner, board_layers=BOARD)[0]


def test_default_golden_all_classes_and_plane_reservations():
    text = patched()
    assert nodes(text, 'use_layer') == [['use_layer', 'F.Cu', 'B.Cu']] * 3
    planes = nodes(text, 'plane')
    assert [p[1] for p in planes] == ['GND', '__ohmai_reserved_layer_PWR']
    assert [p[2][1] for p in planes] == ['GND', 'PWR']
    # Never turn the whole B.Cu shared GND pour into an obstacle; it must be
    # carved by native refill and guarded after import, not called fixed copper.
    assert not any(p[2][1] in ('F.Cu', 'B.Cu') for p in planes)
    assert not nodes(text, 'keepout')
    assert not nodes(text, 'wire_keepout')  # FR 2.5.0 also blocks through-vias
    network = text[slice(*layers.section(text, 'network'))]
    assert ['net', '__ohmai_reserved_layer_PWR'] in sexp.parse(network)
    assert 'Via[0-3]_450:200_um' in text


def test_config_plane_alias_blocks_mislabeled_inner_layer():
    raw = DSN.replace('(layer GND (type power)', '(layer GND (type signal)')
    board = {k: (v[0], 'signal') for k, v in BOARD.items()}
    text, _ = dsn.patch(raw, {}, {'GND': 'In1.Cu'}, [], None, board_layers=board)
    assert nodes(text, 'use_layer') == [['use_layer', 'F.Cu', 'B.Cu']] * 3
    assert '(layer GND (type power)' in text


def test_unconfigured_inner_signal_layer_is_inferred():
    raw = DSN.replace('(layer PWR (type power)', '(layer PWR (type signal)')
    text, _ = dsn.patch(raw, {}, {}, [], None)
    assert nodes(text, 'use_layer') == [['use_layer', 'F.Cu', 'PWR', 'B.Cu']] * 3
    assert [p[2][1] for p in nodes(text, 'plane')] == ['GND']


@pytest.mark.parametrize('signals,inner', [(['F.Cu', 'In2.Cu', 'B.Cu'], None), (None, 'In2.Cu')])
def test_explicit_overrides_resolve_aliases(signals, inner):
    text = patched(signals=signals, inner=inner)
    expected = ['F.Cu', 'PWR', 'B.Cu'] if signals else ['F.Cu', 'B.Cu', 'PWR']
    assert nodes(text, 'use_layer') == [['use_layer', *expected]] * 3
    assert '(layer PWR (type signal)' in text
    assert all(p[2][1] != 'PWR' for p in nodes(text, 'plane'))


def test_unknown_layer_and_empty_signal_list_fail_closed():
    with pytest.raises(ValueError, match='signal layer In9.Cu'):
        patched(signals=['In9.Cu'])
    with pytest.raises(ValueError, match='no routable signal layers'):
        patched(signals=[])


def test_missing_boundary_fails_closed_for_empty_power_layer():
    raw = DSN.replace('(boundary (path pcb 0 0 0 10000 0 10000 10000 0 10000 0 0))', '')
    with pytest.raises(ValueError, match='cannot reserve PWR'):
        dsn.patch(raw, {}, {}, [], None)


def test_quoted_net_parentheses_do_not_break_class_scan():
    text = patched().replace('(class WithoutCircuit /B', '(class WithoutCircuit "Net-(class fake)"')
    constrained = layers.constrain_classes(text, ['F.Cu', 'B.Cu'], None, False, 'InnerOk')
    assert nodes(constrained, 'use_layer') == [['use_layer', 'F.Cu', 'B.Cu']] * 3


def test_board_custom_names(tmp_path):
    pcb = tmp_path / 'fictional.kicad_pcb'
    pcb.write_text('(kicad_pcb (layers (0 "F.Cu" signal) (2 "In1.Cu" power "GND") (4 "In2.Cu" power "PWR") (31 "B.Cu" signal)))')
    assert layers.board_layers(pcb) == BOARD


def test_prepare_warns_for_existing_bad_ses(tmp_path):
    cfg = dict(board=str(tmp_path / "board.kicad_pcb"), autoroute={}, signal_layers=["F.Cu", "B.Cu"])
    cfg.update(planes={'GND': 'In1.Cu'}, copper=None, _signal_layers_explicit=False)
    raw = tmp_path / 'input.dsn'
    raw.write_text(DSN)
    work = tmp_path / 'out'
    work.mkdir(exist_ok=True)
    cfg['work_dir'] = str(work)
    (work / 'demo.ses').write_text('(session x (wire (path PWR 150 0 0 100 100)))')
    # _cfg's board basename is used by prepare.
    from pathlib import Path
    ses = work / (Path(cfg['board']).stem + '.ses')
    if ses.name != 'demo.ses':
        ses.write_text('(session x (wire (path PWR 150 0 0 100 100)))')
    result = steps.prepare_autoroute(cfg, dsn_in=raw)
    assert result['signal_layers'] == ['F.Cu', 'B.Cu']
    assert result['non_signal_layers'] == ['GND', 'PWR']
    assert any('WARNING' in v and 'PWR' in v for v in result['dsn_log'])


def test_ses_bad_clean_twins_ignore_fixed_copper():
    raw = '(session x (wire (path PWR 150 0 0 100 100)) (wire (path GND 150 0 0 100 100) (type fix)))'
    assert layers.ses_non_signal_wires(raw, ['F.Cu', 'B.Cu']) == {'PWR': 1}
    assert layers.ses_non_signal_wires(raw.replace('path PWR', 'path B.Cu'), ['F.Cu', 'B.Cu']) == {}


@pytest.mark.parametrize('layer,ok', [('GND', False), ('B.Cu', True)])
def test_run_refuses_new_non_signal_wires(tmp_path, monkeypatch, layer, ok):
    import subprocess
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    jar = tmp_path / 'freerouting.jar'
    jar.write_bytes(b'pk')
    source = tmp_path / 'fictional.dsn'
    source.write_text('(pcb fictional)')
    ses = tmp_path / 'fictional.ses'
    ses.write_text(f'(session fictional (routes (network_out (net A (wire (path {layer} 150 0 0 100 100))))))')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess([], 0, '', ''))
    monkeypatch.setattr(steps, '_freerouting_java_bin', lambda: 'java')
    monkeypatch.setattr('shutil.which', lambda _: '/usr/bin/java')
    result = steps.run_autoroute(dict(cmd=['java', '-jar', str(jar)], raw_dsn=str(source),
                                     ses_out=str(ses), timeout_sec=1, signal_layers=['F.Cu', 'B.Cu']))
    assert result['ok'] is ok
    assert ('non_signal_layer_wires' in result['fail_reasons']) is not ok
    assert bool(result['warnings']) is not ok


def test_config_distinguishes_inferred_and_explicit_default(tmp_path):
    from types import SimpleNamespace

    from ohmai.layout import config
    project = SimpleNamespace(root=tmp_path, config={}, pcb=tmp_path / 'fictional.kicad_pcb')
    cfg = config.load(project)
    assert cfg['signal_layers'] == ['F.Cu', 'B.Cu']
    assert cfg['_signal_layers_explicit'] is False
    path = tmp_path / 'explicit.yaml'
    path.write_text('signal_layers: [F.Cu, B.Cu]')
    assert config.load(project, path)['_signal_layers_explicit'] is True



def test_subset_keeps_pinless_reserved_plane_nets():
    text, _ = dsn.patch(DSN, {}, {'GND': 'In1.Cu'}, [], None,
                        nets='^/A$', board_layers=BOARD)
    net = sexp.parse(text[slice(*layers.section(text, 'network'))])
    assert ['net', 'GND'] in net
    assert ['net', '__ohmai_reserved_layer_PWR'] in net
    assert not any(c[0] == 'net' and len(c) > 2 and c[1] == 'GND' for c in net if isinstance(c, list))


def test_plane_reservations_are_idempotent():
    first = patched()
    second, _ = dsn.patch(first, {}, {}, [], None, board_layers=BOARD)
    assert nodes(first, 'plane') == nodes(second, 'plane')
    assert nodes(first, 'use_layer') == nodes(second, 'use_layer')



def test_custom_outer_layer_pour_is_not_a_dedicated_inner_plane():
    raw = DSN.replace('F.Cu', 'Top').replace('B.Cu', 'Bottom')
    board = {'F.Cu': ('Top', 'signal'), 'In1.Cu': ('GND', 'power'),
             'In2.Cu': ('PWR', 'power'), 'B.Cu': ('Bottom', 'signal')}
    text, _ = dsn.patch(raw, {}, {}, [], None, board_layers=board)
    assert nodes(text, 'use_layer') == [['use_layer', 'Top', 'Bottom']] * 3
    assert all(p[2][1] != 'Bottom' for p in nodes(text, 'plane'))
