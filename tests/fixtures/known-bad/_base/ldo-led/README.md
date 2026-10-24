Shared base for netlist-level known-bad cases. Cases list it after `golden:ldo-led` (the golden board's
sources: constraints, BOM, expected nets, layout yamls); this base adds the board's netlist as YAML
(`board.net.yaml`, compiled to `board.net`), as kicad-cli 9.0.2 exports it with libraries 9.0.9.1.
A KiCad test checks it still matches a fresh export of the golden board.
