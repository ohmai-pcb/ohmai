# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""No KiCad required: immutable triplets, recovery paths and real-fill evidence."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ohmai.layout import refill, steps


@pytest.fixture
def source(tmp_path):
    board = tmp_path / 'input.kicad_pcb'
    board.write_text('(kicad_pcb)')
    board.with_suffix('.kicad_pro').write_text('{}')
    board.with_suffix('.kicad_dru').write_text('(version 1)')
    return board


def fake_run(command, **kwargs):
    raw, filled = Path(command[3]), Path(command[4])
    assert raw.with_suffix('.kicad_pro').is_file()
    assert raw.with_suffix('.kicad_dru').read_text() == '(version 1)'
    rules = json.loads(raw.with_suffix('.kicad_pro').read_text())
    assert rules['board']['design_settings']['rules']['min_connection'] >= .127
    assert raw.read_text() == '(kicad_pcb)'
    filled.write_text('(kicad_pcb)')
    return subprocess.CompletedProcess(command, 0, 'saved', '')


def test_same_stem_rules_exist_before_child_load(source, monkeypatch):
    monkeypatch.setattr(refill.subprocess, 'run', fake_run)
    before = [source.with_suffix(e).read_bytes() for e in ('.kicad_pcb', '.kicad_pro', '.kicad_dru')]
    output = source.parent / 'output.kicad_pcb'
    result = refill.run(source, output, source.parent / 'evidence')
    assert result['ok'] and result['attempts'][0]['mode'] == 'clean'
    assert '.SetProject(' not in refill.SCRIPT
    assert 'SaveBoard(sys.argv[2], b, True)' in refill.SCRIPT
    assert before == [source.with_suffix(e).read_bytes() for e in ('.kicad_pcb', '.kicad_pro', '.kicad_dru')]
    assert result['filled_polygon_sha256'] == refill.polygon_digest(output)


@pytest.mark.parametrize('crashes', [1, 2, 3])
def test_fresh_subprocess_and_immutable_input_every_attempt(source, monkeypatch, crashes):
    seen = []
    def run(command, **kwargs):
        raw, output = Path(command[3]), Path(command[4])
        seen.append((raw, command[-1]))
        assert raw.read_text() == '(kicad_pcb)'
        if len(seen) <= crashes:
            raw.write_text('damaged input from fictional native crash')
            output.write_text('partial output')
            raise subprocess.CalledProcessError(-11, command, '', 'fictional trace: SetProject')
        return fake_run(command, **kwargs)
    monkeypatch.setattr(refill.subprocess, 'run', run)
    output = source.parent / 'output.kicad_pcb'
    evidence = source.parent / 'evidence'
    if crashes == 3:
        with pytest.raises(subprocess.CalledProcessError):
            refill.run(source, output, evidence)
        assert not output.exists()
    else:
        assert refill.run(source, output, evidence)['ok']
    assert len({raw for raw, _ in seen}) == min(crashes + 1, 3)
    assert [mode for _, mode in seen] == ['clean', 'single-cpu', 'single-cpu-rebuild'][:len(seen)]
    logs = json.loads((evidence / 'refill-attempts.json').read_text())
    assert sum(row['returncode'] == -11 for row in logs) == crashes
    assert all(Path(row['directory'], 'filled.kicad_pcb').exists() for row in logs)


def test_non_crash_failure_not_retried(source, monkeypatch):
    def fail(command, **kwargs):
        raise subprocess.CalledProcessError(1, command, '', 'bad rules')
    monkeypatch.setattr(refill.subprocess, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        refill.run(source, source.parent / 'out.kicad_pcb', source.parent / 'evidence')
    assert len(json.loads((source.parent / 'evidence/refill-attempts.json').read_text())) == 1


def test_success_without_board_is_not_accepted(source, monkeypatch):
    monkeypatch.setattr(refill.subprocess, 'run', lambda c, **kw: subprocess.CompletedProcess(c, 0, '', ''))
    with pytest.raises(RuntimeError, match='without a filled PCB'):
        refill.run(source, source.parent / 'out.kicad_pcb', source.parent / 'evidence')


def test_never_refill_in_place(source):
    with pytest.raises(ValueError, match='scratch copy'):
        refill.run(source, source, source.parent / 'evidence')


def test_missing_project_fails_closed(source):
    source.with_suffix('.kicad_pro').unlink()
    with pytest.raises(RuntimeError, match='alongside'):
        refill.run(source, source.parent / 'out.kicad_pcb', source.parent / 'evidence')


def test_output_and_retry_log_backups(source, monkeypatch):
    monkeypatch.setattr(refill.subprocess, 'run', fake_run)
    output = source.parent / 'out.kicad_pcb'
    output.write_text('old PCB')
    evidence = source.parent / 'evidence'
    evidence.mkdir()
    (evidence / 'refill-attempts.json').write_text('old log')
    refill.run(source, output, evidence)
    assert next(source.parent.glob('out.kicad_pcb.*.bak')).read_text() == 'old PCB'
    assert next(evidence.glob('refill-attempts.json.*.bak')).read_text() == 'old log'


def test_polygon_digest_ignores_ring_order_not_copper(tmp_path):
    def board(name, points):
        path = tmp_path / name
        path.write_text('(kicad_pcb (zone (uuid zone) (filled_polygon (layer F.Cu) (pts '
                        + ' '.join(f'(xy {x} {y})' for x, y in points) + '))))')
        return path
    a = board('a.pcb', [(0, 0), (1, 0), (1, 1)])
    b = board('b.pcb', [(1, 1), (1, 0), (0, 0)])
    c = board('c.pcb', [(0, 0), (2, 0), (1, 1)])
    assert refill.polygon_digest(a) == refill.polygon_digest(b)
    assert refill.polygon_digest(a) != refill.polygon_digest(c)


def test_pour_uses_shared_fresh_refill_without_live_write(source, monkeypatch):
    calls = []
    def run(board, dest, evidence, floor):
        calls.append((board, dest, floor))
        dest.write_text('(kicad_pcb)')
        return {'ok': True}
    monkeypatch.setattr(refill, 'run', run)
    monkeypatch.setattr(steps.pourgeom, 'extract_filled_pour', lambda pcb, net, layer: (None, [], None))
    monkeypatch.setattr(steps.pourgeom, 'attach_features', lambda *args: [])
    monkeypatch.setattr(steps.pourgeom, 'measure', lambda *args: {'pieces': 1})
    monkeypatch.setattr(steps.pourgeom, 'verdict', lambda *args: True)
    monkeypatch.setattr(steps.L, 'load', lambda p: SimpleNamespace(footprints={}))
    before = source.read_bytes()
    result = steps.run_pour(dict(board=str(source), work_dir=str(source.parent / 'out')), net='GND', layer='F.Cu')
    assert result['ok'] and calls[0][1] != source
    assert result['measured_board'] == str(calls[0][1])
    assert source.read_bytes() == before


def test_polygon_digest_ignores_generated_zone_uuid(tmp_path):
    first = tmp_path / 'first.pcb'
    second = tmp_path / 'second.pcb'
    first.write_text('(kicad_pcb (zone (uuid a) (net_name GND) (layer F.Cu) '
                     '(polygon (pts (xy 0 0) (xy 1 0) (xy 1 1))) '
                     '(filled_polygon (layer F.Cu) (pts (xy 0 0) (xy 1 0) (xy 1 1)))))')
    second.write_text(first.read_text().replace('(uuid a)', '(uuid regenerated)'))
    assert refill.polygon_digest(first) == refill.polygon_digest(second)


def test_anonymous_zone_multiplicity_is_not_lost(tmp_path):
    first = tmp_path / 'first.pcb'
    second = tmp_path / 'second.pcb'
    zone = '(zone (layer F.Cu) (net_name GND) (filled_polygon (layer F.Cu) (pts (xy 0 0) (xy 1 0) (xy 1 1))))'
    first.write_text('(kicad_pcb '+zone+')')
    second.write_text('(kicad_pcb '+zone+' '+zone+')')
    assert refill.polygon_digest(first) != refill.polygon_digest(second)
