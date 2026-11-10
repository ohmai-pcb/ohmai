# SPDX-License-Identifier: Apache-2.0
"""Synthetic transaction tests; no KiCad installation needed."""
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ohmai.layout import changes, native_guard
from ohmai.layout.changes_worker import SCRIPT

VIA = dict(id='stitch-1', op='via.add', net='GND', at=[2, 3], diameter=.45, drill=.2, layers=['F.Cu', 'B.Cu'])


@pytest.fixture
def project(tmp_path):
    board = tmp_path / 'test.kicad_pcb'
    board.write_text('(kicad_pcb (net 1 "GND"))')
    board.with_suffix('.kicad_pro').write_text('{"board":{"design_settings":{"rules":{"min_connection":0.127}}}}')
    base = tmp_path / 'base.kicad_pcb'
    base.write_bytes(board.read_bytes())
    path = tmp_path / 'changes.json'
    path.write_text(json.dumps(dict(schema_version=1, base=dict(commit='abcdef0', board_file=base.name,
                                                              sha256=changes.digest(base)), operations=[VIA])))
    return board, path, dict(board=str(board), work_dir=str(tmp_path / 'out'), rules_resolved={})


def edit(path, function):
    value = json.loads(path.read_text())
    function(value)
    path.write_text(json.dumps(value))


def test_schema_snapshot_and_ids(project):
    board, path, _ = project
    result = changes.load(path, board, .15)
    assert result['new_copper'] == []
    edit(path, lambda d: d['operations'].append(VIA))
    with pytest.raises(changes.ChangeError, match='unique'):
        changes.load(path, board, .15)


@pytest.mark.parametrize('mutate', [lambda d: d.update(schema_version=2),
                                  lambda d: d.update(typo=True),
                                  lambda d: d['operations'][0].update(at=[1]),
                                  lambda d: d['operations'][0].update(layers=['In1.Cu', 'B.Cu']),
                                  lambda d: d['operations'][0].update(diameter=-1)])
def test_schema_refuses_invalid(project, mutate):
    board, path, _ = project
    edit(path, mutate)
    with pytest.raises(changes.ChangeError, match='invalid change list'):
        changes.load(path, board, .15)


def test_base_hash_mandatory(project):
    board, path, _ = project
    (path.parent / 'base.kicad_pcb').write_text('changed')
    with pytest.raises(changes.ChangeError, match='SHA-256'):
        changes.load(path, board, .15)


def test_history_ignores_fill_cache_but_detects_changed_copper(tmp_path):
    a = tmp_path / 'a.pcb'
    b = tmp_path / 'b.pcb'
    text = '(kicad_pcb (net 1 GND) (zone (uuid z) (net 1) (polygon (pts (xy 0 0))) %s))'
    a.write_text(text % '(filled_polygon (pts (xy 1 2)))')
    b.write_text(text % '(filled_polygon (pts (xy 5 6)))')
    assert changes._fingerprints(a) == changes._fingerprints(b)
    b.write_text(b.read_text().replace('(xy 0 0)', '(xy 3 4)'))
    assert changes._fingerprints(a) != changes._fingerprints(b)


def mock_native(monkeypatch, conflict=False):
    def run(data, source, dest, work):
        if conflict:
            return dict(ok=False, conflicts=[dict(id='stitch-1', reason='new copper conflict')])
        dest.write_text('candidate')
        return dict(ok=True, conflicts=[])
    monkeypatch.setattr(changes, '_native', run)


def mock_guard(monkeypatch, after=None, before=None, ok=True, concurrent=None):
    def validate(source, candidate, nets, floor, evidence_dir):
        assert not nets and floor >= .127
        evidence_dir.mkdir()
        paths = []
        for name, report in [('before', before or {}), ('after', after or {})]:
            folder = evidence_dir / name
            folder.mkdir()
            p = folder / 'drc.json'
            p.write_text(json.dumps(report))
            (folder / 'filled.kicad_pcb').write_text('measured-filled-board')
            paths.append(str(p))
        if concurrent:
            concurrent()
        return dict(ok=ok, before_drc=paths[0], after_drc=paths[1])
    monkeypatch.setattr(native_guard, 'validate', validate)


def test_dry_run_full_native_no_write(project, monkeypatch):
    board, path, cfg = project
    old = board.read_bytes()
    mock_native(monkeypatch)
    mock_guard(monkeypatch)
    result = changes.apply(cfg, path, dry_run=True)
    assert result['ok'] and not result['applied'] and board.read_bytes() == old
    assert Path(result['evidence_dir'], 'after-drc.json').exists()
    assert 'ohmai layout apply-changes' in Path(result['evidence_dir'], 'RESULTS.md').read_text()


def test_apply_commits_measured_board_with_timestamp_backup(project, monkeypatch):
    board, path, cfg = project
    old = board.read_bytes()
    mock_native(monkeypatch)
    mock_guard(monkeypatch)
    result = changes.apply(cfg, path)
    assert result['ok'] and result['applied']
    assert board.read_text() == 'measured-filled-board'
    assert Path(result['backup']).read_bytes() == old
    assert Path(result['backup']).name.endswith('.bak')


@pytest.mark.parametrize('kind,description', [('shorting_items', ''), ('tracks_crossing', ''),
                                            ('hole_clearance', ''), ('hole_near_hole', ''),
                                            ('connection_width', ''), ('clearance', 'SAFETY rule'),
                                            ('clearance', 'ordinary')])
def test_aggregate_guard_rejects_even_old_uuid_regressions(project, monkeypatch, kind, description):
    board, path, cfg = project
    old = board.read_bytes()
    mock_native(monkeypatch)
    mock_guard(monkeypatch, after=dict(violations=[dict(type=kind, description=description, items=[{'uuid': 'old'}])]))
    result = changes.apply(cfg, path)
    assert not result['ok'] and not result['applied'] and board.read_bytes() == old
    assert result['aggregate_regressions']
    assert Path(result['evidence_dir'], 'after-drc.json').exists()


def test_worse_net_rollback_keeps_full_after_json(project, monkeypatch):
    board, path, cfg = project
    old = board.read_bytes()
    mock_native(monkeypatch)
    after = dict(unconnected_items=[dict(items=[dict(description='Pad [/sense]')])])
    mock_guard(monkeypatch, after=after, ok=False)
    result = changes.apply(cfg, path)
    assert not result['ok'] and board.read_bytes() == old
    assert json.loads(Path(result['evidence_dir'], 'after-drc.json').read_text()) == after


def test_conflicts_listed_without_board_write(project, monkeypatch):
    board, path, cfg = project
    old = board.read_bytes()
    mock_native(monkeypatch, conflict=True)
    result = changes.apply(cfg, path)
    assert not result['ok'] and result['conflicts'][0]['id'] == VIA['id']
    assert board.read_bytes() == old


def test_concurrent_change_refuses_write(project, monkeypatch):
    board, path, cfg = project
    mock_native(monkeypatch)
    mock_guard(monkeypatch, concurrent=lambda: board.write_text('concurrent edit'))
    result = changes.apply(cfg, path)
    assert not result['ok'] and 'changed during validation' in result['error']
    assert board.read_text() == 'concurrent edit'


def test_duplicate_backup_does_not_overwrite(tmp_path):
    p = tmp_path / 'board.pcb'
    p.write_text('one')
    a = changes.backup(p)
    p.write_text('two')
    b = changes.backup(p)
    assert a != b and a.read_text() == 'one' and b.read_text() == 'two'


@pytest.mark.parametrize('failures', [1, 2, 3])
def test_refill_segfault_retry_evidence(project, monkeypatch, failures):
    board, _, _ = project
    calls = []
    def run(command, **kwargs):
        if '-c' in command:
            calls.append(command)
            if len(calls) <= failures:
                raise subprocess.CalledProcessError(-11, command, output='', stderr='SIGSEGV')
        else:
            Path(command[command.index('-o') + 1]).write_text('{}')
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(native_guard.subprocess, 'run', run)
    work = board.parent / 'drc'
    if failures == 3:
        with pytest.raises(subprocess.CalledProcessError):
            native_guard._report(board, work, .127)
    else:
        assert native_guard._report(board, work, .127) == {}
    logs = json.loads((work / 'refill-attempts.json').read_text())
    assert len(calls) == min(3, failures + 1)
    assert sum(a['returncode'] == -11 for a in logs) == failures


def test_worker_is_subprocess_only_and_syntax_valid():
    compile(SCRIPT, '<worker>', 'exec')
    assert 'import pcbnew' in SCRIPT and 'poly.thisown = False' in SCRIPT


def test_native_response_error_is_fail_closed(project, monkeypatch):
    board, path, cfg = project
    monkeypatch.setattr(changes.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout='', stderr='crash', returncode=-11))
    result = changes.apply(cfg, path)
    assert not result['ok'] and 'native apply failed' in result['error']


@pytest.fixture
def worker(monkeypatch):
    """Run the actual worker control flow against a tiny synthetic native API."""
    import sys

    class Point:
        def __init__(self, x=0, y=0):
            self.x, self.y = x, y
        def __eq__(self, other):
            return self.x == other.x and self.y == other.y
    class Shape:
        def __init__(self, pos):
            self.pos = pos
        def Collide(self, other, gap):
            return abs(self.pos.x - other.pos.x) + abs(self.pos.y - other.pos.y) < 1 + gap
    class Track:
        def __init__(self, board):
            self.net, self.layer, self.width = 1, 0, .2
            self.start, self.end = Point(2, 3), Point(3, 3)
            self.m_Uuid = SimpleNamespace(AsString=lambda: str(id(self)))
        def GetNetname(self): return {1: 'GND', 2: 'SIGNAL'}[self.net]
        def GetNetCode(self): return self.net
        def SetNetCode(self, v): self.net = v
        def GetLayerSet(self): return SimpleNamespace(Seq=lambda: [self.layer])
        def GetLayer(self): return self.layer
        def SetLayer(self, v): self.layer = v
        def IsLocked(self): return False
        def GetEffectiveShape(self, layer): return Shape(self.start)
        def GetStart(self): return self.start
        def GetEnd(self): return self.end
        def SetStart(self, v): self.start = v
        def SetEnd(self, v): self.end = v
        def GetWidth(self, *args): return self.width
        def SetWidth(self, *args): self.width = args[-1]
    class Via(Track):
        def GetPosition(self): return self.start
        def SetPosition(self, v): self.start = v
        def SetDrill(self, v): self.drill = v
        def GetDrillValue(self): return self.drill
        def SetViaType(self, v): pass
        def SetLayerPair(self, *args): pass
        def TopLayer(self): return 0
        def BottomLayer(self): return 31
    class Arc(Track):
        pass
    class Zone:
        pass
    board = SimpleNamespace(GetTracks=lambda: [existing], GetFootprints=lambda: [], Zones=lambda: [],
                            GetEnabledLayers=lambda: SimpleNamespace(Seq=lambda: [0, 31]),
                            GetStandardLayerName=lambda i: 'F.Cu' if i == 0 else 'B.Cu',
                            GetNetsByNetcode=lambda: {n: SimpleNamespace(GetNetname=lambda n=n: 'GND' if n == 1 else 'SIGNAL',
                                                                       GetNetCode=lambda n=n: n) for n in (1, 2)},
                            Add=lambda i: None, Remove=lambda i: None, SetProject=lambda p: None)
    existing = Track(board)
    existing.net = 2
    fake = SimpleNamespace(LoadBoard=lambda src: board, PCB_TRACK=Track, PCB_VIA=Via, PCB_ARC=Arc, ZONE=Zone,
                           LSET=SimpleNamespace(AllCuMask=lambda: SimpleNamespace(Seq=lambda: [0, 31])),
                           ToMM=lambda v: v, FromMM=lambda v: v, VECTOR2I=Point, VIATYPE_THROUGH=0, F_Cu=0, B_Cu=31,
                           GetSettingsManager=lambda: SimpleNamespace(LoadProject=lambda p: True, Prj=lambda: None),
                           SaveBoard=lambda out, b: None)
    monkeypatch.setitem(sys.modules, 'pcbnew', fake)
    namespace = {'__name__': 'isolated_worker_test'}
    exec(compile(SCRIPT, '<worker>', 'exec'), namespace)
    key = existing.m_Uuid.AsString()
    def run(op, newer=False):
        return namespace['run'](dict(operations=[op], clearance=.15, base_fingerprints={key: 'old'},
                                     current_fingerprints={key: 'new' if newer else 'old'},
                                     new_copper=[key] if newer else []), 'input.pcb', 'output.kicad_pcb')
    return run, existing, namespace


@pytest.mark.parametrize('newer', [False, True])
def test_worker_refuses_overlapping_foreign_copper(worker, newer):
    run, _, _ = worker
    result = run(VIA, newer)
    assert not result['ok']
    reasons = str(result['conflicts'])
    assert ('since base commit' if newer else 'foreign-net') in reasons


def test_worker_allows_same_net_attachment(worker):
    run, item, _ = worker
    item.net = 1
    assert run(VIA)['ok']


@pytest.mark.parametrize('problem', ['uuid', 'coordinates', 'net', 'stale'])
def test_worker_delete_validates_identity_geometry_and_history(worker, problem):
    run, item, _ = worker
    expect = dict(net='SIGNAL', layer='F.Cu', start=[2, 3], end=[3, 3], width=.2)
    op = dict(id='rip', op='track.delete', uuid=item.m_Uuid.AsString(), expect=expect)
    if problem == 'uuid':
        op['uuid'] = 'missing'
    if problem == 'coordinates':
        expect['start'] = [5, 6]
    if problem == 'net':
        expect['net'] = 'GND'
    result = run(op, newer=problem == 'stale')
    assert not result['ok'] and result['conflicts'][0]['id'] == 'rip'


def test_worker_valid_delete(worker):
    run, item, _ = worker
    op = dict(id='rip', op='track.delete', uuid=item.m_Uuid.AsString(),
              expect=dict(net='SIGNAL', layer='F.Cu', start=[2, 3], end=[3, 3], width=.2))
    assert run(op)['ok']


@pytest.mark.parametrize('update,reason', [({'net': 'MISSING'}, 'unknown net'),
                                         ({'drill': .6}, 'smaller'),
                                         ({'op': 'track.add', 'layer': 'Disabled.Cu'}, 'disabled')])
def test_worker_unknown_net_and_bad_geometry(worker, update, reason):
    run, _, _ = worker
    assert reason in str(run(dict(VIA, **update))['conflicts'])


def test_worker_detects_new_copper_without_serialized_uuid(worker):
    _, item, namespace = worker
    # Loader gave this existing track an ID, but raw file had none. There is no
    # corresponding geometry in the immutable base: it MUST be a stale blocker.
    result = namespace['run'](dict(operations=[VIA], clearance=.15, base_fingerprints={},
                                  current_fingerprints={}, new_copper=[]), 'input.pcb', 'output.kicad_pcb')
    assert not result['ok'] and 'since base commit' in str(result['conflicts'])


@pytest.mark.parametrize('extra', [False, True])
def test_worker_anonymous_base_geometry_uses_multiplicity(worker, extra):
    _, item, ns = worker
    p = ns['p']
    base = p.LoadBoard('base')
    current = SimpleNamespace(**vars(base))
    second = type(item)(None)
    second.net = 2
    current.GetTracks = lambda: [item, second] if extra else [item]
    p.LoadBoard = lambda source: base if source == 'base' else current
    result = ns['run'](dict(operations=[VIA], base_path='base', clearance=.15, base_fingerprints={},
                            current_fingerprints={}, new_copper=[]), 'current', 'output.kicad_pcb')
    assert not result['ok']  # Foreign copper, regardless of its history.
    assert ('since base commit' in str(result['conflicts'])) == extra


def test_results_command_quotes_spaces(project, monkeypatch):
    import shlex

    _, path, cfg = project
    spaced = path.with_name('space plan.json')
    path.rename(spaced)
    mock_native(monkeypatch)
    mock_guard(monkeypatch)
    result = changes.apply(cfg, spaced, dry_run=True)
    text = Path(result['evidence_dir'], 'RESULTS.md').read_text()
    command = text.split('Apply command: `')[1].split('`')[0]
    assert shlex.split(command) == ['ohmai', 'layout', 'apply-changes', str(spaced)]
