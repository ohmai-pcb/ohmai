"""SKiDL side of `ohmai check skidl-netlist`. Runs ONLY as a separate process (python _skidl_runner.py spec.json):
SKiDL imports InSpice (GPL-3.0-or-later), so it must never be imported into the ohmai process.

Input spec.json: {sym_dir, out, parts: [{ref, lib, name, value, footprint}], nets: [{name, nodes: ["U1.5"]}],
wild: [{name, refs: ["U1"]}], nc: ["U1.4"]}. Output: the SKiDL netlist at `out` and JSON on stdout:
{"errors": [...], "skidl": version}.
"""

import contextlib
import io
import json
import sys


def main() -> int:
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    errors = []
    noise = io.StringIO()
    with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
        import skidl
        from skidl import Net, Part, generate_netlist, lib_search_paths, set_default_tool

        tool = getattr(skidl, "KICAD9", None) or skidl.KICAD8
        set_default_tool(tool)
        lib_search_paths[tool] = [spec["sym_dir"]]
        parts = {}
        for p in spec["parts"]:
            try:
                parts[p["ref"]] = Part(p["lib"], p["name"], ref=p["ref"], tag=p["ref"], value=p.get("value") or None,
                                       footprint=p.get("footprint") or None)
            except Exception as e:  # noqa: BLE001 (SKiDL raises many types)
                errors.append(f"{p['ref']}: SKiDL cannot load symbol {p['lib']}:{p['name']} ({type(e).__name__}: {e})")

        def pin(node):
            ref, num = node.split(".", 1)
            if ref not in parts:
                errors.append(f"{node}: part {ref} not in the schematic")
                return None
            pn = parts[ref].p[num]
            if pn is None or (isinstance(pn, list) and not pn):
                errors.append(f"{node}: symbol {spec_lib(ref)} has no pin {num}")
                return None
            return pn

        def spec_lib(ref):
            p = next(x for x in spec["parts"] if x["ref"] == ref)
            return f"{p['lib']}:{p['name']}"

        for n in spec["nets"]:
            net = Net(n["name"])
            for node in n["nodes"]:
                pn = pin(node)
                if pn is not None:
                    net += pn
            for ref in n.get("refs", ()):
                if ref in parts:
                    net += parts[ref].pins
        generate_netlist(file_=spec["out"])
    print(json.dumps({"errors": errors, "skidl": skidl.__version__}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
