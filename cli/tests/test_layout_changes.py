# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
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
            Path(command[4]).write_text('(kicad_pcb)')
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
                           SaveBoard=lambda out, b, skip: True)
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


def test_native_apply_does_not_switch_project_after_load():
    assert '.SetProject(' not in SCRIPT
    assert 'SaveBoard(output, board, True)' in SCRIPT


ZONE_ADD = dict(id='corridor-in2', op='zone.add', name='corridor', net='GND', layer='In2.Cu',
                polygon=[[0, 0], [10, 0], [10, 4], [0, 4]], priority=1, clearance=.3, min_thickness=.25,
                pad_connection='solid', island_removal='always')
ZONE_UUID = '11111111-2222-3333-4444-555555555555'
ZONE_EXPECT = dict(net='GND', layer='B.Cu', polygon=[[0, 0], [10, 0], [10, 4], [0, 4]])


@pytest.mark.parametrize('op', [ZONE_ADD,
                                dict(id='grow', op='zone.edit', uuid=ZONE_UUID, expect=ZONE_EXPECT,
                                     set=dict(polygon=[[0, 0], [12, 0], [12, 6], [0, 6]], name='wider')),
                                dict(id='drop', op='zone.delete', uuid=ZONE_UUID, expect=ZONE_EXPECT)])
def test_schema_accepts_zone_add_resize_delete(project, op):
    board, path, _ = project
    edit(path, lambda d: d.update(operations=[op]))
    assert changes.load(path, board, .15)['operations'] == [op]


@pytest.mark.parametrize('mutate', [lambda op: op.update(polygon=[[0, 0], [1, 1]]),
                                    lambda op: op.pop('min_thickness'),
                                    lambda op: op.update(pad_connection='glued'),
                                    lambda op: op.update(layers=['F.Cu', 'B.Cu']),
                                    lambda op: op.update(name='')])
def test_schema_refuses_bad_zone_add(project, mutate):
    board, path, _ = project
    op = json.loads(json.dumps(ZONE_ADD))
    mutate(op)
    edit(path, lambda d: d.update(operations=[op]))
    with pytest.raises(changes.ChangeError, match='invalid change list'):
        changes.load(path, board, .15)


@pytest.mark.parametrize('op', [dict(id='e', op='zone.edit', uuid=ZONE_UUID, expect=ZONE_EXPECT, set={}),
                                dict(id='e', op='zone.edit', uuid=ZONE_UUID, expect=dict(net='GND'),
                                     set=dict(priority=2)),
                                dict(id='d', op='zone.delete', uuid='not-a-uuid', expect=ZONE_EXPECT)])
def test_schema_refuses_bad_zone_edit_delete(project, op):
    board, path, _ = project
    edit(path, lambda d: d.update(operations=[op]))
    with pytest.raises(changes.ChangeError, match='invalid change list'):
        changes.load(path, board, .15)


def test_zone_add_dry_run_and_apply_use_guard(project, monkeypatch):
    board, path, cfg = project
    edit(path, lambda d: d.update(operations=[ZONE_ADD]))
    mock_native(monkeypatch)
    mock_guard(monkeypatch)
    before = board.read_bytes()
    result = changes.apply(cfg, path, dry_run=True)
    assert result['ok'] and not result['applied'] and board.read_bytes() == before
    result = changes.apply(cfg, path)
    assert result['ok'] and result['applied'] and board.read_text() == 'measured-filled-board'


def test_zone_resize_regression_rolls_back(project, monkeypatch):
    board, path, cfg = project
    op = dict(id='grow', op='zone.edit', uuid=ZONE_UUID, expect=ZONE_EXPECT,
              set=dict(polygon=[[0, 0], [12, 0], [12, 6], [0, 6]]))
    edit(path, lambda d: d.update(operations=[op]))
    mock_native(monkeypatch)
    unconnected = dict(unconnected_items=[dict(items=[dict(description='Pad 1 [PWR] of U1')])])
    mock_guard(monkeypatch, after=unconnected, ok=False)
    before = board.read_bytes()
    result = changes.apply(cfg, path)
    assert not result['ok'] and not result['applied'] and board.read_bytes() == before


def test_zone_worker_names_zone():
    assert "z.SetZoneName(data['name'])" in SCRIPT and 'name=item.GetZoneName()' in SCRIPT


def _kicad_python():
    import os
    import shutil
    exe = os.environ.get('OHMAI_KICAD_PYTHON', '/usr/bin/python3')
    if not shutil.which('kicad-cli') or not Path(exe).exists():
        return None
    ok = subprocess.run([exe, '-c', 'import pcbnew'], capture_output=True).returncode == 0
    return exe if ok else None


SYNTHETIC_BOARD = r'''
import sys, pcbnew as p
b = p.BOARD()
b.SetCopperLayerCount(4)
for name in ('GND', 'PWR'):
    b.Add(p.NETINFO_ITEM(b, name))
mm = p.FromMM
corners = [(0, 0), (40, 0), (40, 20), (0, 20)]
for i in range(4):
    s = p.PCB_SHAPE(b); s.SetShape(p.SHAPE_T_SEGMENT); s.SetLayer(p.Edge_Cuts); s.SetWidth(mm(.1))
    (x0, y0), (x1, y1) = corners[i], corners[(i + 1) % 4]
    s.SetStart(p.VECTOR2I(mm(x0), mm(y0))); s.SetEnd(p.VECTOR2I(mm(x1), mm(y1))); b.Add(s)
for x in (5, 35):
    t = p.PCB_TRACK(b); t.SetLayer(p.F_Cu); t.SetWidth(mm(1)); t.SetNet(b.FindNet('PWR'))
    t.SetStart(p.VECTOR2I(mm(x), mm(10))); t.SetEnd(p.VECTOR2I(mm(x + .1), mm(10))); b.Add(t)
z = p.ZONE(b); z.SetLayer(p.B_Cu); z.SetNet(b.FindNet('GND')); z.SetMinThickness(mm(.25))
o = p.SHAPE_POLY_SET(); o.NewOutline()
for x, y in [(1, 1), (39, 1), (39, 19), (1, 19)]:
    o.Append(mm(x), mm(y))
z.SetOutline(o); o.thisown = False; b.Add(z)
p.SaveBoard(sys.argv[1], b)
'''


@pytest.mark.skipif(_kicad_python() is None, reason='needs kicad-cli and KiCad Python (pcbnew)')
def test_native_zone_add_resize_delete_on_synthetic_board(tmp_path):
    """Real worker + refill + DRC on a fictional 4-layer board: In2 pour, resize, delete."""
    from ohmai.layout import zones
    board = tmp_path / 'fictional.kicad_pcb'
    subprocess.run([_kicad_python(), '-c', SYNTHETIC_BOARD, str(board)], check=True, capture_output=True)
    base = tmp_path / 'base.kicad_pcb'
    base.write_bytes(board.read_bytes())
    gnd = zones.list_zones(board)[0]
    cfg = dict(board=str(board), work_dir=str(tmp_path / 'out'), rules_resolved={})

    def run(ops, dry_run):
        path = tmp_path / 'changes.json'
        path.write_text(json.dumps(dict(schema_version=1, base=dict(
            commit='abcdef0', board_file=base.name, sha256=changes.digest(base)), operations=ops)))
        return changes.apply(cfg, path, dry_run=dry_run)

    corridor = [dict(ZONE_ADD, net='PWR', polygon=[[4, 8], [36, 8], [36, 12], [4, 12]]),
                dict(VIA, id='v1', net='PWR', at=[5, 10], diameter=.8, drill=.4),
                dict(VIA, id='v2', net='PWR', at=[35, 10], diameter=.8, drill=.4),
                dict(id='grow', op='zone.edit', uuid=gnd['uuid'], expect=gnd['expect'],
                     set=dict(polygon=[[1, 1], [39, 1], [39, 7], [1, 7]], name='ground'))]
    old = board.read_bytes()
    dry = run(corridor, True)
    assert dry['ok'] and not dry['applied'] and board.read_bytes() == old, dry
    assert dry['before']['unconnected'] == {'PWR': 1} and dry['after']['unconnected'] == {}
    done = run(corridor, False)
    assert done['ok'] and done['applied'], done
    listed = {(z['net'], z['layers'][0]): z for z in zones.list_zones(board)}
    assert listed[('PWR', 'In2.Cu')]['name'] == 'corridor'
    assert listed[('GND', 'B.Cu')]['expect']['polygon'] == [[1, 1], [39, 1], [39, 7], [1, 7]]
    # A stale zone.delete (old base geometry) is refused without touching the board.
    applied = board.read_bytes()
    stale = run([dict(id='drop', op='zone.delete', uuid=gnd['uuid'], expect=gnd['expect'])], True)
    assert not stale['ok'] and board.read_bytes() == applied


@pytest.mark.parametrize('op', [dict(id='d', op='zone.delete', uuid=ZONE_UUID, match='uuid'),
                                dict(id='d', op='zone.delete', uuid=ZONE_UUID, match='uuid', expect=dict(net='GND')),
                                dict(id='d', op='zone.delete', uuid=ZONE_UUID, expect=ZONE_EXPECT,
                                     polygon_tolerance=.01),
                                dict(id='e', op='zone.edit', uuid=ZONE_UUID, match='uuid', set=dict(priority=2))])
def test_schema_accepts_uuid_only_and_tolerance(project, op):
    board, path, _ = project
    edit(path, lambda d: d.update(operations=[op]))
    assert changes.load(path, board, .15)['operations'] == [op]


@pytest.mark.parametrize('op', [dict(id='d', op='zone.delete', uuid=ZONE_UUID),
                                dict(id='d', op='zone.delete', uuid=ZONE_UUID, match='geometry', expect=dict(net='GND')),
                                dict(id='d', op='zone.delete', uuid=ZONE_UUID, match='fuzzy'),
                                dict(id='d', op='zone.delete', uuid=ZONE_UUID, expect=ZONE_EXPECT, polygon_tolerance=1)])
def test_schema_refuses_missing_expect_without_uuid_mode(project, op):
    board, path, _ = project
    edit(path, lambda d: d.update(operations=[op]))
    with pytest.raises(changes.ChangeError, match='invalid change list'):
        changes.load(path, board, .15)


@pytest.mark.parametrize('op', [dict(ZONE_ADD, polygon=[[0, 0], [10, 4], [10, 0], [0, 4]]),
                                dict(id='e', op='zone.edit', uuid=ZONE_UUID, expect=ZONE_EXPECT,
                                     set=dict(polygon=[[0, 0], [12, 6], [12, 0], [0, 6]])),
                                dict(ZONE_ADD, polygon=[[0, 0], [5, 0], [10, 0]])])
def test_self_intersecting_zone_polygon_rejected_before_native(project, op, monkeypatch):
    board, path, cfg = project
    edit(path, lambda d: d.update(operations=[op]))
    with pytest.raises(changes.ChangeError, match=r"operation (corridor-in2|e): .*(not a simple polygon|zero area)"):
        changes.load(path, board, .15)
    called = []
    monkeypatch.setattr(changes, '_native', lambda *a: called.append(a))
    result = changes.apply(cfg, path, dry_run=True)
    assert not result['ok'] and not called and 'polygon' in result['error']


def _worker_namespace():
    import sys
    import types
    fake = types.ModuleType('pcbnew')
    saved = sys.modules.get('pcbnew')
    sys.modules['pcbnew'] = fake
    try:
        ns = {'__name__': 'ohmai_worker_test'}
        exec(compile(SCRIPT, 'changes_worker', 'exec'), ns)
    finally:
        if saved is None:
            sys.modules.pop('pcbnew')
        else:
            sys.modules['pcbnew'] = saved
    return ns


def test_worker_polygon_tolerance_and_rotation():
    ns = _worker_namespace()
    actual = [[1.000001, 1.0], [39.0, 1.0], [39.0, 7.000004], [1.0, 7.0]]
    match = ns['polygon_matches']
    assert match(actual, [[1.0, 1.0], [39.0, 1.0], [39.0, 7.0], [1.0, 7.0]], .005)
    assert match(actual, [[39.0, 7.0], [1.0, 7.0], [1.0, 1.0], [39.0, 1.0]], .005)  # other start vertex
    assert match(actual, [[1.004, 1.0], [39.0, 1.0], [39.0, 7.0], [1.0, 7.0]], .005)
    assert not match(actual, [[1.01, 1.0], [39.0, 1.0], [39.0, 7.0], [1.0, 7.0]], .005)
    assert not match(actual, [[1.0, 1.0], [1.0, 7.0], [39.0, 7.0], [39.0, 1.0]], .005)  # reversed winding
    assert not match(actual, actual[:3], .005)


@pytest.mark.skipif(_kicad_python() is None, reason='needs kicad-cli and KiCad Python (pcbnew)')
def test_native_zone_delete_rounded_expect_and_uuid_only(tmp_path):
    from ohmai.layout import zones
    board = tmp_path / 'fictional.kicad_pcb'
    subprocess.run([_kicad_python(), '-c', SYNTHETIC_BOARD, str(board)], check=True, capture_output=True)
    base = tmp_path / 'base.kicad_pcb'
    base.write_bytes(board.read_bytes())
    gnd = zones.list_zones(board)[0]
    cfg = dict(board=str(board), work_dir=str(tmp_path / 'out'), rules_resolved={})

    def run(op):
        path = tmp_path / 'changes.json'
        path.write_text(json.dumps(dict(schema_version=1, base=dict(
            commit='abcdef0', board_file=base.name, sha256=changes.digest(base)), operations=[op])))
        return changes.apply(cfg, path, dry_run=True)

    off = dict(gnd['expect'], polygon=[[x + .003, y - .003] for x, y in gnd['expect']['polygon']])
    assert run(dict(id='d', op='zone.delete', uuid=gnd['uuid'], expect=off)).get('error') is None
    far = dict(gnd['expect'], polygon=[[x + .02, y] for x, y in gnd['expect']['polygon']])
    assert 'does not match expect' in str(run(dict(id='d', op='zone.delete', uuid=gnd['uuid'], expect=far)))
    loose = dict(id='d', op='zone.delete', uuid=gnd['uuid'], expect=far, polygon_tolerance=.05)
    assert 'does not match expect' not in str(run(loose))
    assert 'does not match expect' not in str(run(dict(id='d', op='zone.delete', uuid=gnd['uuid'], match='uuid')))
    wrong_net = dict(id='d', op='zone.delete', uuid=gnd['uuid'], match='uuid', expect=dict(net='PWR'))
    assert 'does not match expect' in str(run(wrong_net))
