# SPDX-License-Identifier: Apache-2.0
"""Pure regression coverage for native candidate gates; no KiCad required."""
from pathlib import Path

import pytest

from ohmai.layout import native_guard as guard


def _open(net):
    return {'items': [{'description': f'Pad 1 [{net}] of fictional U1'}]}


def test_guard_rejects_a_split_foreign_pour():
    before = {'unconnected_items': [_open('TARGET')]}
    after = {'unconnected_items': [_open('VIN_BB')]}
    result = guard.compare(before, after, {'TARGET'}, 0.127, set())
    assert not result['ok']
    assert result['worse_nets'] == {'VIN_BB': [0, 1]}
    assert result['after']['unconnected'].get('TARGET', 0) == 0


def test_guard_rejects_geometric_neck_even_with_wide_track():
    warning = {'type': 'connection_width', 'description':
               'Copper connection too narrow (minimum 0.1270 mm; actual 0.0175 mm)',
               'items': [{'uuid': 'new', 'description': 'Track [TARGET] width 0.2 mm'}]}
    result = guard.compare({}, {'violations': [warning]}, {'TARGET'}, 0.127, set())
    assert not result['ok'] and result['new_connection_width'] == [warning]


def test_guard_preserves_existing_warnings_and_distinguishes_old_item_pair_noise():
    warning = {'type': 'connection_width', 'description': 'actual 0.1000 mm', 'items': []}
    clearance = {'type': 'clearance', 'description': 'existing pair chosen after refill',
                 'items': [{'uuid': 'old-a'}, {'uuid': 'old-b'}]}
    result = guard.compare({'violations': [warning]}, {'violations': [warning, clearance]},
                           {'TARGET'}, 0.127, {'old-a', 'old-b'})
    assert result['ok']
    clearance['items'][0]['uuid'] = 'new-route'
    assert not guard.compare({}, {'violations': [clearance]}, {'TARGET'}, 0.127,
                             {'old-a', 'old-b'})['ok']


def test_new_safety_edge_violation_cannot_escape_the_guard():
    after = {'violations': [dict(type='copper_edge_clearance',
        description="SAFETY edge setback: actual 0.425 mm", items=[{'uuid': 'new-track'}])]}
    result = guard.compare({}, after, {'SIG'}, .127, {'old-pad'})
    assert not result['ok']
    assert len(result['new_copper_violations']) == 1


def test_native_refill_loads_colocated_project_without_switch(monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = []
    board = SimpleNamespace(SetProject=lambda project: pytest.fail('unsafe project swap'),
                            GetDesignSettings=lambda: SimpleNamespace(m_MinConn=.127),
                            Zones=lambda: [], BuildConnectivity=lambda: calls.append(('connectivity',)))
    fake = SimpleNamespace(LoadBoard=lambda path: calls.append(('board', path)) or board,
                           ToMM=lambda value: value, GetBuildVersion=lambda: 'fictional-test',
                           ZONE_FILLER=lambda b: SimpleNamespace(Fill=lambda z: calls.append(('fill',)) or True),
                           SaveBoard=lambda path, b, skip: calls.append(('save', path, skip)) or True)
    monkeypatch.setitem(sys.modules, 'pcbnew', fake)
    monkeypatch.setattr(sys, 'argv', ['refill', 'input.pcb', 'filled.pcb', '.127', 'clean'])
    exec(guard._REFILL, {})
    assert calls == [('board', 'input.pcb'), ('fill',), ('connectivity',), ('save', 'filled.pcb', True)]


def test_native_report_refuses_rules_silently_reset_by_kicad(tmp_path, monkeypatch):
    import json

    import pytest

    board = tmp_path / 'board.kicad_pcb'
    board.write_text('(kicad_pcb)')
    project = board.with_suffix('.kicad_pro')
    project.write_text(json.dumps({'board': {'design_settings': {'rules': {'min_connection': .127}}}}))

    def reset_rules(command, **kwargs):
        if '-c' in command:
            Path(command[3]).with_suffix('.kicad_pro').write_text('{}')
        from subprocess import CompletedProcess
        return CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(guard.subprocess, 'run', reset_rules)
    with pytest.raises(RuntimeError, match='immutable project settings'):
        guard._report(board, tmp_path / 'check', .127)


def test_rejected_guard_preserves_full_reports_and_copper(tmp_path, monkeypatch):
    import json
    board = tmp_path / 'board.kicad_pcb'
    candidate = tmp_path / 'candidate.kicad_pcb'
    board.write_text('(kicad_pcb (uuid old))')
    candidate.write_text('(kicad_pcb (uuid new))')
    reports = [{'unconnected_items': [_open('SIG')]},
               {'unconnected_items': [_open('SIG'), _open('SIG')]}]

    def report(source, work, floor):
        work.mkdir()
        data = reports[0 if source == board else 1]
        (work / 'drc.json').write_text(json.dumps(data))
        (work / 'input.kicad_pcb').write_bytes(source.read_bytes())
        return data

    monkeypatch.setattr(guard, '_report', report)
    monkeypatch.setattr(guard.shutil, 'which', lambda _: 'kicad-cli')
    result = guard.validate(board, candidate, {'SIG'}, .127, tmp_path / 'evidence')
    assert not result['ok']
    assert json.loads(__import__('pathlib').Path(result['after_drc']).read_text()) == reports[1]
    # Simulate rollback/removal of scratch input: durable evidence is still complete.
    candidate.unlink()
    work = __import__('pathlib').Path(result['evidence_dir'])
    assert (work / 'after' / 'input.kicad_pcb').read_text() == '(kicad_pcb (uuid new))'
    assert json.loads((work / 'verification.json').read_text())['target_worse'] == {'SIG': [1, 2]}


def test_baseline_refill_crash_still_preserves_proposal(tmp_path, monkeypatch):
    import pytest
    board = tmp_path / 'reference.kicad_pcb'
    candidate = tmp_path / 'candidate.kicad_pcb'
    board.write_text('(kicad_pcb)')
    candidate.write_text('(kicad_pcb (segment proposed))')
    monkeypatch.setattr(guard.shutil, 'which', lambda _: 'kicad-cli')

    def crash(*args):
        raise RuntimeError('fictional native refill failure')

    monkeypatch.setattr(guard, '_report', crash)
    evidence = tmp_path / 'evidence'
    with pytest.raises(RuntimeError, match='refill failure'):
        guard.validate(board, candidate, {'SIG'}, .127, evidence)
    run = next(evidence.iterdir())
    assert (run / 'proposal.kicad_pcb').read_bytes() == candidate.read_bytes()
    assert 'refill failure' in (run / 'failure.json').read_text()
