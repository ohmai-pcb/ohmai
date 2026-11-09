"""DSN edits (ohmai.layout.dsn) on a FICTIONAL Specctra file shaped like KiCad 9's export. Needs shapely."""

from __future__ import annotations

import pytest

pytest.importorskip("shapely")

from ohmai.layout import dsn  # noqa: E402

DSN = """(pcb fixture.dsn
  (structure
    (layer F.Cu
      (type signal)
    )
    (layer In1.Cu
      (type signal)
    )
    (layer B.Cu
      (type signal)
    )
    (plane GND (polygon In1.Cu 0  100000 -100000  120000 -100000  120000 -120000  100000 -120000
            100000 -100000))
    (keepout "" (polygon signal 0  110000 -110000  111000 -110000  111000 -111000  110000 -111000))
    (via "Via[0-2]_450:200_um")
  )
  (library
    (image kb:EDGE_fixture
      (pin Oval[A]Pad_2000x1000_um 1 0 0)
      (pin Rect[T]Pad_500x500_um 2 0 -2000)
    )
    (padstack Oval[A]Pad_900x3000_um
      (shape (path F.Cu 900  0 -1000  0 1000))
      (attach off)
    )
  )
  (network
    (net /A
      (pins J1-1 U1-1)
    )
    (net /B
      (pins J1-2 U1-2)
    )
    (net GND
      (pins U1-3)
    )
    (net VIN
      (pins U1-4)
    )
    (class kicad_default /A /B
      (circuit
        (use_via "Via[0-2]_450:200_um")
      )
      (rule
        (width 150)
        (clearance 100)
      )
    )
    (class Power GND VIN VOUT
      (circuit
        (use_via "Via[0-2]_450:200_um")
      )
      (rule
        (width 300)
        (clearance 150)
      )
    )
  )
  (wiring
    (wire (path F.Cu 250  101000 -101000  102000 -101000)(net GND)(type route))
    (wire (path F.Cu 150  103000 -103000  104000 -103000)(net /A)(type route))
    (via "Via[0-2]_450:200_um"  102000 -101000 (net GND)(type route))
    (via "Via[0-2]_450:200_um"  104000 -103000 (net /A)(type route))
  )
)
"""

CFG = {"drop_pins": [{"padstack": "Oval[A]Pad_2000x1000_um", "component": "J1"}],
       "keepouts": [[100, 100, 100.8, 120]], "grow_cutouts": 0.25, "plane_keepout_margin": 0.025,
       "plane_via_keepout": 0.5, "class_split": {"class": "Power", "keep": ["VIN"], "name": "PowerHi", "width": 0.2},
       "widen_padstacks": [{"padstack": "Oval[A]Pad_900x3000_um", "from": 900, "to": 1000}],
       "inner_class": {"name": "InnerOk", "width": 0.15, "clearance": 0.1}}
COPPER = ["F.Cu", "In1.Cu", "B.Cu"]


def test_all_steps():
    s, log = dsn.patch(DSN, CFG, {"GND": "In1.Cu"}, COPPER, ["F.Cu", "B.Cu"])
    # 1: the half-pad and its net reference are gone, the other pin stays
    assert "Oval[A]Pad_2000x1000_um" not in s and "J1-1" not in s and "U1-1)" in s and "J1-2" in s
    assert '(keepout "" (polygon signal 0  100000 -100000 100800 -100000 100800 -120000 100000 -120000' in s
    # 2: the cut-out keepout is grown (no longer the bare square)
    assert "110000 -110000  111000 -110000" not in s
    # 3: GND copper -> keepouts, GND net and plane statement out, GND out of its class
    assert "(net GND)" not in s and "(net GND\n" not in s and "(plane GND" not in s
    assert '(keepout "" (circle signal 500 102000 -101000))' in s and '(keepout "" (polygon F.Cu 0' in s
    assert "(class Power VIN VOUT" not in s
    # 4: In1 carries no signals
    assert "(layer In1.Cu\n      (type power)" in s
    # 5: the signal copper is fixed
    assert "(net /A)(type fix))" in s and "(type route)" not in s
    # keepouts sit before the structure via line
    assert s.index('(keepout "" (circle') < s.index('(via "Via[0-2]_450:200_um")\n  )')
    # 6: Power split: VOUT thin at 200, VIN keeps 300 in PowerHi
    assert "(class Power VOUT" in s and "(class PowerHi VIN" in s
    thin = s[s.index("(class Power VOUT"):]
    assert "(width 200)" in thin.split("(class PowerHi")[0] and "(width 300)" in thin.split("(class PowerHi")[1]
    # 7: padstack widened
    assert "(shape (path F.Cu 1000  0 -1000  0 1000))" in s
    assert log[0].startswith("dropped 1 ") and "class Power split: 1 thin, 1 kept" in log


def test_inner_layer_only_for_listed_nets():
    s, log = dsn.patch(DSN, {}, {}, COPPER, ["F.Cu", "In1.Cu", "B.Cu"], inner_layer="In1.Cu", inner_nets="^/B$")
    assert "(class InnerOk /B\n" in s and "(class kicad_default /A\n" in s
    assert s.count("(use_layer F.Cu B.Cu)") == 2          # kicad_default and Power; not InnerOk
    assert log == ["In1.Cu allowed for 1 nets: /B"]


def test_subset_and_missing_layer():
    s, _ = dsn.patch(DSN, {}, {}, COPPER, COPPER, nets="^/", exclude="B")
    assert "(net /A\n" in s and "(net /B\n" not in s and "(net VIN\n" not in s
    with pytest.raises(ValueError, match="signal layer In9.Cu"):
        dsn.patch(DSN, {}, {}, COPPER + ["In9.Cu"], COPPER)


def test_fixed_wire_chain_is_coalesced_without_changing_geometry():
    from ohmai import sexp
    text = '''(pcb fictional
      (parser (string_quote "))
      (wiring
        (wire (path F.Cu 600 0 0 50 0) (net VBUS) (type fix))
        (wire (path F.Cu 600 50 0 750 -700) (net VBUS) (type fix))
        (wire (path F.Cu 600 750 -700 1000 -700 1200 -900) (net VBUS) (type fix))
      ))'''
    out, n = dsn.normalize_wiring(text)
    assert n == 2
    tree = sexp.parse(out[out.index('(wiring'):out.rfind(')')])
    wires = sexp.find(tree, 'wire')
    assert len(wires) == 1
    path = sexp.first(wires[0], 'path')
    assert len(path[3:]) == 10  # All five original vertices survive, including the tiny stub.
    assert dsn.normalize_wiring(out) == (out, 0)


def test_wire_chains_preserve_branches_widths_and_layers():
    text = '''(pcb fictional (wiring
      (wire (path F.Cu 150 0 0 100 0)(net A)(type fix))
      (wire (path F.Cu 150 100 0 200 0)(net A)(type fix))
      (wire (path F.Cu 150 100 0 100 100)(net A)(type fix))
      (wire (path B.Cu 150 200 0 300 0)(net A)(type fix))
      (wire (path F.Cu 200 200 0 300 0)(net A)(type fix))
    ))'''
    assert dsn.normalize_wiring(text) == (text, 0)


def test_wrapped_fractional_copper_is_fixed():
    text = DSN.replace('104000 -103000)(net /A)(type route)',
                       '104000.5 -103000)\n(net "Net-(U1-A)")\n(type route)')
    out, _ = dsn.patch(text, {}, {}, COPPER, COPPER)
    assert '(type route)' not in out
