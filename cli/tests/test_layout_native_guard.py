"""Pure regression coverage for native candidate gates; no KiCad required."""
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
