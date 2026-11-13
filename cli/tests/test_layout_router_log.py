# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Fictional router runs; no Java, KiCad, remote Git, or source project writes."""
import argparse
import json
from types import SimpleNamespace

import pytest

from ohmai.layout import cli, router_log, steps


def test_append_archives_each_dsn_and_records_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(router_log, '_version', lambda command: 'fictional JDK')
    dsn = tmp_path / 'route.dsn'
    dsn.write_text('(pcb fictional)')
    prepared = dict(raw_dsn=str(dsn), patched_dsn=str(dsn), nets='SIG', jar='freerouting-2.2.4.jar')
    failed = dict(ok=False, fail_reasons=['freerouting_npe'], stderr_tail=(
        'java.lang.NullPointerException: to_trace_entries is null\n'
        ' at ShapeSearchTree.merge_entries_in_front'))
    a = router_log.append_run(tmp_path, {}, prepared, failed, {})
    dsn.write_text('(pcb modified)')
    b = router_log.append_run(tmp_path, {}, {}, {}, {}, 'preparation failed')
    records = [json.loads(line) for line in (tmp_path/'out/routing/freerouting.jsonl').read_text().splitlines()]
    assert len(records) == 2 and a['id'] != b['id']
    assert records[0]['crash'] and records[0]['upstream_candidate']
    assert records[0]['drc_before'] is None and records[0]['drc_delta'] is None
    assert (tmp_path/records[0]['artifacts']['patched_dsn']['path']).read_text() == '(pcb fictional)'
    assert records[1]['error'] == 'preparation failed'
    assert len((tmp_path/'out/routing/freerouting-log.md').read_text().splitlines()) == 2


def test_measured_clearance_and_safety_deltas_and_invalid_measurement(tmp_path, monkeypatch):
    monkeypatch.setattr(router_log, '_version', lambda command: None)
    for name, values in [('before', []), ('after', [dict(type='clearance', description='SAFETY', items=[])])]:
        (tmp_path/f'{name}.json').write_text(json.dumps({'violations': values, 'unconnected_items': []}))
    r = router_log.append_run(tmp_path, {}, {}, {'ok': True}, {'drc_before': 'before.json','drc_after': 'after.json'})
    assert r['drc_delta']['safety'] == r['drc_delta']['nonzone_clearance'] == 1
    assert r['upstream_candidate'] and not r['promotion_allowed']
    r = router_log.append_run(tmp_path, {}, {}, {}, {'drc_after': 'missing.json'})
    assert r['measurement_errors'] and r['drc_after'] is None


@pytest.mark.parametrize('failure', ['prepare', 'execute', 'npe', 'prepare_only', 'interrupt'])
def test_cli_always_logs_after_project_identification(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(cli, '_project', lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(cli, '_cfg', lambda *a: {})
    monkeypatch.setattr(router_log, '_version', lambda command: None)
    def prepare(*a, **kwargs):
        if failure == 'prepare':
            raise ValueError('fictional export failure')
        return {}
    def execute(*a, **kwargs):
        if failure == 'interrupt':
            raise KeyboardInterrupt
        if failure == 'execute':
            raise RuntimeError('fictional JVM failure')
        return dict(ok=False, fail_reasons=['freerouting_npe'])
    monkeypatch.setattr(steps, 'prepare_autoroute', prepare)
    monkeypatch.setattr(steps, 'run_autoroute', execute)
    args=argparse.Namespace(dsn=None, passes=None, opt_passes=None, timeout=None, via_costs=None,
                            threads=None, nets='SIG', exclude_nets=None, prepare_only=failure=='prepare_only', json=True)
    assert cli.cmd_autoroute(args) == (0 if failure == 'prepare_only' else 130 if failure == 'interrupt' else 1)
    assert len((tmp_path/'out/routing/freerouting.jsonl').read_text().splitlines()) == 1


def test_measured_report_cannot_hide_a_worse_other_net(tmp_path, monkeypatch):
    monkeypatch.setattr(router_log, '_version', lambda command: None)
    before = {'violations': [], 'unconnected_items': []}
    after = {'violations': [], 'unconnected_items': [{'items': [{'description': 'Pad [OTHER]'}]}]}
    for name,report in [('before',before),('after',after)]:
        (tmp_path/f'{name}.json').write_text(json.dumps(report))
    r = router_log.append_run(tmp_path, {}, {}, {'ok':True}, {'drc_before':'before.json','drc_after':'after.json'})
    assert r['worse_nets'] == {'OTHER':[0,1]}
    assert r['drc_gate_passed'] is False
