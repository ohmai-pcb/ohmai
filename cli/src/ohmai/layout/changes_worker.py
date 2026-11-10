# SPDX-License-Identifier: Apache-2.0
"""Native worker script, executed only in an isolated KiCad subprocess."""

SCRIPT = r'''
# SPDX-License-Identifier: Apache-2.0
"""Isolated KiCad worker. Execute as a file; NEVER import into the OhmAI process."""
import json
import sys
from collections import Counter

import pcbnew as p


def xy(v):
    return [round(p.ToMM(v.x), 6), round(p.ToMM(v.y), 6)]


def point(v):
    return p.VECTOR2I(p.FromMM(v[0]), p.FromMM(v[1]))


def identity(item):
    return item.m_Uuid.AsString()


def polygon(z):
    out = z.Outline()
    # v1 only edits single-outline zones without cutouts; never silently lose holes.
    if out.OutlineCount() != 1 or out.HoleCount(0):
        raise ValueError('zone edit requires one simple outline, without holes')
    line = out.COutline(0)
    return [xy(line.CPoint(i)) for i in range(line.PointCount())]


def describe(item, kind):
    common = {'net': item.GetNetname()}
    if kind == 'via':
        return dict(common, at=xy(item.GetPosition()), diameter=p.ToMM(item.GetWidth(p.F_Cu)),
                    drill=p.ToMM(item.GetDrillValue()), layers=[board.GetStandardLayerName(item.TopLayer()),
                                                              board.GetStandardLayerName(item.BottomLayer())])
    if kind == 'track':
        if isinstance(item, p.PCB_ARC):
            raise ValueError('track.delete accepts straight segments, not arcs')
        return dict(common, start=xy(item.GetStart()), end=xy(item.GetEnd()),
                    width=p.ToMM(item.GetWidth()), layer=board.GetStandardLayerName(item.GetLayer()))
    conn = {p.ZONE_CONNECTION_FULL: 'solid', p.ZONE_CONNECTION_THERMAL: 'thermal',
            p.ZONE_CONNECTION_NONE: 'none'}
    return dict(common, layer=board.GetStandardLayerName(item.GetLayer()), polygon=polygon(item),
                priority=item.GetAssignedPriority(), clearance=p.ToMM(item.GetLocalClearance()),
                min_thickness=p.ToMM(item.GetMinThickness()), pad_connection=conn[item.GetPadConnection()],
                thermal_gap=p.ToMM(item.GetThermalReliefGap()), thermal_spoke=p.ToMM(item.GetThermalReliefSpokeWidth()),
                island_removal='always' if item.GetIslandRemovalMode() == p.ISLAND_REMOVAL_MODE_ALWAYS else 'never')


def matches(actual, expected):
    if isinstance(expected, dict):
        return all(k in actual and matches(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(map(lambda ab: matches(*ab), zip(actual, expected, strict=True)))
    if isinstance(expected, (float, int)):
        return abs(actual - expected) < 0.000002
    return actual == expected


def layers(item):
    return set(item.GetLayerSet().Seq()) & copper_layers


def shapes(item):
    return {layer: (item.Outline().Clone() if isinstance(item, p.ZONE)
                    else item.GetEffectiveShape(layer)) for layer in layers(item)}


def collision(a, b, clearance=0):
    return any(a[layer].Collide(b[layer], clearance) for layer in a.keys() & b.keys())


def configure_zone(z, data):
    if 'polygon' in data:
        poly = p.SHAPE_POLY_SET()
        poly.NewOutline()
        for q in data['polygon']:
            poly.Append(point(q).x, point(q).y)
        if poly.IsSelfIntersecting() or abs(poly.Area()) < 1:
            raise ValueError('self-intersecting or zero-area zone polygon')
        z.SetOutline(poly)
        poly.thisown = False  # SetOutline takes ownership; avoid a dangling C++ pointer.
    setters = {'priority': ('SetAssignedPriority', int), 'clearance': ('SetLocalClearance', p.FromMM),
               'min_thickness': ('SetMinThickness', p.FromMM), 'thermal_gap': ('SetThermalReliefGap', p.FromMM),
               'thermal_spoke': ('SetThermalReliefSpokeWidth', p.FromMM)}
    for key, (method, convert) in setters.items():
        if key in data:
            getattr(z, method)(convert(data[key]))
    if 'pad_connection' in data:
        z.SetPadConnection({'solid': p.ZONE_CONNECTION_FULL, 'thermal': p.ZONE_CONNECTION_THERMAL,
                            'none': p.ZONE_CONNECTION_NONE}[data['pad_connection']])
    if 'island_removal' in data:
        z.SetIslandRemovalMode(p.ISLAND_REMOVAL_MODE_ALWAYS if data['island_removal'] == 'always'
                               else p.ISLAND_REMOVAL_MODE_NEVER)


def geometry_key(item):
    # Appended S-expression copper can lack UUIDs. LoadBoard invents random
    # UUIDs for it, so classify anonymous copper by native geometry, not IDs.
    if isinstance(item, p.PCB_VIA):
        return ('via', item.GetNetname(), tuple(xy(item.GetPosition())), item.GetWidth(p.F_Cu),
                item.GetDrillValue(), item.TopLayer(), item.BottomLayer())
    if isinstance(item, p.PCB_TRACK):
        ends = tuple(sorted((tuple(xy(item.GetStart())), tuple(xy(item.GetEnd())))))
        mid = tuple(xy(item.GetMid())) if isinstance(item, p.PCB_ARC) else ()
        return ('track', item.GetNetname(), item.GetLayer(), ends, mid, item.GetWidth())
    return None


def run(data, source, output):
    global board, copper_layers
    base_geometry = Counter()
    if data.get('base_path'):
        base_board = p.LoadBoard(data['base_path'])
        base_geometry.update(geometry_key(item) for item in base_board.GetTracks())
    board = p.LoadBoard(source)
    copper_layers = set(board.GetEnabledLayers().Seq()) & set(p.LSET.AllCuMask().Seq())
    layer_ids = {board.GetStandardLayerName(i): i for i in copper_layers}
    nets = {net.GetNetname(): net.GetNetCode() for net in board.GetNetsByNetcode().values()}
    tracks = list(board.GetTracks())
    for item in tracks:
        key = identity(item)
        if key in data['base_fingerprints'] and key in data['current_fingerprints']:
            geometry = geometry_key(item)
            if base_geometry[geometry]:
                base_geometry[geometry] -= 1
    for item in tracks:
        key = identity(item)
        if key in data['current_fingerprints']:
            continue
        geometry = geometry_key(item)
        if base_geometry[geometry]:
            base_geometry[geometry] -= 1
        else:
            data['new_copper'].append(key)
    pads = [pad for fp in board.GetFootprints() for pad in fp.Pads()]
    zones = list(board.Zones())
    items = {identity(t): t for t in tracks + pads + zones}
    footprints = {f.GetReference(): f for f in board.GetFootprints()}
    old = [(identity(t), t.GetNetname(), isinstance(t, p.ZONE), shapes(t)) for t in tracks + pads + zones]
    conflicts, proposed, removed = [], [], set()
    modified = set()
    for op in data['operations']:
        try:
            kind, action = op['op'].split('.')
            if action in ('delete', 'edit'):
                key = op['uuid']
                if key in modified:
                    raise ValueError('multiple operations on the same UUID')
                modified.add(key)
                item = items.get(key)
                cls = {'via': p.PCB_VIA, 'track': p.PCB_TRACK, 'zone': p.ZONE}[kind]
                if item is None or not isinstance(item, cls) or (kind == 'track' and isinstance(item, p.PCB_VIA)):
                    raise ValueError('missing UUID or wrong item type: ' + key)
                if data['base_fingerprints'].get(key) != data['current_fingerprints'].get(key) or key not in data['base_fingerprints']:
                    raise ValueError('item changed or was added since base commit: ' + key)
                if item.IsLocked():
                    raise ValueError('item is locked: ' + key)
                if not matches(describe(item, kind), op['expect']):
                    raise ValueError('UUID geometry/net does not match expect: ' + key)
                removed.add(key)
                if action == 'delete':
                    board.Remove(item)
                    continue
                configure_zone(item, op['set'])
            elif kind == 'footprint':
                fp = footprints.get(op['ref'])
                if fp is None:
                    raise ValueError('unknown footprint: ' + op['ref'])
                key = identity(fp)
                if key in modified:
                    raise ValueError('multiple operations on footprint: ' + op['ref'])
                modified.add(key)
                if fp.IsLocked():
                    raise ValueError('footprint is locked: ' + op['ref'])
                if data['base_fingerprints'].get(key) != data['current_fingerprints'].get(key) or key not in data['base_fingerprints']:
                    raise ValueError('footprint changed since base commit: ' + op['ref'])
                if not matches(dict(at=xy(fp.GetPosition()), rotation=fp.GetOrientationDegrees()), op['expect']):
                    raise ValueError('footprint position/rotation does not match expect')
                removed.update(identity(pad) for pad in fp.Pads())
                fp.SetPosition(point(op['to']))
                proposed.extend((op, pad, shapes(pad)) for pad in fp.Pads())
                continue
            else:
                if op['net'] not in nets or not op['net']:
                    raise ValueError('unknown net: ' + op['net'])
                if kind == 'via':
                    if op['drill'] >= op['diameter']:
                        raise ValueError('via drill must be smaller than diameter')
                    item = p.PCB_VIA(board)
                    item.SetPosition(point(op['at']))
                    item.SetWidth(p.F_Cu, p.FromMM(op['diameter']))
                    item.SetDrill(p.FromMM(op['drill']))
                    item.SetViaType(p.VIATYPE_THROUGH)
                    item.SetLayerPair(p.F_Cu, p.B_Cu)
                else:
                    if op['layer'] not in layer_ids:
                        raise ValueError('unknown/disabled copper layer: ' + op['layer'])
                    if kind == 'track':
                        if point(op['start']) == point(op['end']):
                            raise ValueError('zero-length segment')
                        item = p.PCB_TRACK(board)
                        item.SetStart(point(op['start']))
                        item.SetEnd(point(op['end']))
                        item.SetWidth(p.FromMM(op['width']))
                    else:
                        item = p.ZONE(board)
                        configure_zone(item, op)
                    item.SetLayer(layer_ids[op['layer']])
                item.SetNetCode(nets[op['net']])
                if identity(item) in items:
                    raise ValueError('operation UUID already on board')
                board.Add(item)
            proposed.append((op, item, shapes(item)))
        except (ValueError, KeyError) as error:
            conflicts.append(dict(id=op['id'], reason=str(error)))
    # Check real copper shapes on their actual layers, not bounding boxes. Pours
    # may overlap old copper: refill carves the required clearance around it.
    for op, item, shape in proposed:
        for key, net, is_zone, other_shape in old:
            if key in removed or (is_zone and key not in data['new_copper']):
                continue
            newer = key in data['new_copper']
            clearances = data.get('net_clearances', {})
            required = max(clearances.get(net, data['clearance']), clearances.get(item.GetNetname(), data['clearance']))
            gap = p.FromMM(required) if newer and net != item.GetNetname() else 0
            if not collision(shape, other_shape, gap):
                continue
            duplicate = False
            existing = items[key]
            if isinstance(item, p.PCB_VIA) and isinstance(existing, p.PCB_VIA):
                duplicate = item.GetPosition() == existing.GetPosition()
            elif isinstance(item, p.PCB_TRACK) and not isinstance(item, p.PCB_VIA) and type(item) is type(existing):
                duplicate = ({tuple(xy(item.GetStart())), tuple(xy(item.GetEnd()))}
                             == {tuple(xy(existing.GetStart())), tuple(xy(existing.GetEnd()))})
            if (newer and net != item.GetNetname()) or duplicate or (op['op'] != 'zone.add' and op['op'] != 'zone.edit'
                                                                      and not is_zone and net != item.GetNetname()):
                conflicts.append(dict(id=op['id'], copper_uuid=key, net=net,
                                      reason='conflict with copper added/changed since base commit' if newer
                                      else ('duplicate copper' if duplicate else 'overlaps existing foreign-net copper')))
    for i, (op, item, shape) in enumerate(proposed):
        for other, t, s in proposed[:i]:
            if op['id'] != other['id'] and not isinstance(item, p.ZONE) and not isinstance(t, p.ZONE):
                if item.GetNetname() != t.GetNetname() and collision(shape, s):
                    conflicts.append(dict(id=op['id'], other_id=other['id'], reason='proposed foreign-net copper overlaps'))
                elif isinstance(item, p.PCB_VIA) and isinstance(t, p.PCB_VIA) and item.GetPosition() == t.GetPosition():
                    conflicts.append(dict(id=op['id'], other_id=other['id'], reason='duplicate proposed via'))
    if conflicts:
        return dict(ok=False, conflicts=conflicts)
    manager = p.GetSettingsManager()
    if not manager.LoadProject(str(output).replace('.kicad_pcb', '.kicad_pro')):
        raise RuntimeError('cannot load project rules')
    board.SetProject(manager.Prj())
    p.SaveBoard(output, board)
    created = {}
    for op, item, _ in proposed:
        created.setdefault(op['id'], []).append(identity(item))
    return dict(ok=True, conflicts=[], operations=len(data['operations']), created_items=created, newer_copper=data['new_copper'])


if __name__ == '__main__':
    try:
        result = run(json.loads(open(sys.argv[1]).read()), sys.argv[2], sys.argv[3])
        print(json.dumps(result))
    except Exception as error:
        print(json.dumps(dict(ok=False, conflicts=[dict(id='native', reason=str(error))])))
        sys.exit(1)

'''
