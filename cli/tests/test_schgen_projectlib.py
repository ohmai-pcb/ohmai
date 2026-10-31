"""schgen Lib: project hardware/lib fallback and space-indented (imported) symbols."""
from pathlib import Path

import pytest

from ohmai import libimport
from ohmai.schgen import Lib

SPACED = '''(kicad_symbol_lib
  (version 20211014)
  (generator https://github.com/uPesy/easyeda2kicad.py)
  (symbol "BQX"
    (in_bom yes)
    (on_board yes)
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "BQX" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (symbol "BQX_0_1"
      (pin input line (at 0 0 0) (length 2.54) (name "EN" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
    )
  )
)
'''


def _dirs(tmp_path: Path):
    stock, proj = tmp_path / "stock", tmp_path / "lib"
    stock.mkdir()
    proj.mkdir()
    (proj / "OhmAI_Project.kicad_sym").write_text(SPACED)
    return stock, proj


def test_project_lib_fallback_and_space_indent(tmp_path):
    stock, proj = _dirs(tmp_path)
    lib = Lib(stock, proj)
    raw = lib.raw("OhmAI_Project", "BQX")
    assert raw.lstrip().startswith('(symbol "BQX"') and raw.endswith(")") and '"BQX_0_1"' in raw
    emb = lib.embedded("OhmAI_Project:BQX")
    assert '(symbol "OhmAI_Project:BQX"' in emb
    s = lib.sym("OhmAI_Project:BQX")
    assert s is not None


def test_missing_lib_points_at_fetch(tmp_path):
    stock, proj = _dirs(tmp_path)
    with pytest.raises(SystemExit, match="kicad-libs fetch"):
        Lib(stock, proj).raw("Nope", "X")
    with pytest.raises(SystemExit):
        Lib(stock).raw("OhmAI_Project", "BQX")  # no project dir -> stock only


def test_model_paths_rewritten_to_kiprjmod():
    t = ('(footprint "F"\n  (model "/tmp/tmp7wb/ee.3dshapes/Q.wrl"\n    (offset (xyz 0 0 0)))\n'
         '  (model "C:\\\\x\\\\Q.step")\n  (model "${KICAD9_3DMODEL_DIR}/Package_DFN_QFN.3dshapes/Q.step")\n)')
    out = libimport.fix_model_paths(t, "OhmAI_Project")
    assert '(model "${KIPRJMOD}/lib/OhmAI_Project.3dshapes/Q.wrl"' in out
    assert '(model "${KIPRJMOD}/lib/OhmAI_Project.3dshapes/Q.step"' in out
    assert "${KICAD9_3DMODEL_DIR}" in out and "/tmp/" not in out
