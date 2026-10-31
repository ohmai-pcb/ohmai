"""Pinned official KiCad libraries per project.

ohmai.yaml:
    kicad:
      libraries: "9.0.9.1"    # tag of gitlab.com/kicad/libraries/kicad-symbols and kicad-footprints

`ohmai kicad-libs install` downloads that tag into $OHMAI_KICAD_LIBS (default ~/.cache/ohmai/kicad-libs).
`ohmai kicad-libs tables` writes hardware/sym-lib-table and hardware/fp-lib-table that point at
${OHMAI_KICAD_SYMBOLS} / ${OHMAI_KICAD_FOOTPRINTS}. OhmAI sets those variables for every kicad-cli run,
so ERC compares the embedded symbols with exactly the pinned library (lib_symbol_issues) on any machine.
"""

from __future__ import annotations

import os
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from ohmai.project import Project

URL = "https://gitlab.com/kicad/libraries/{repo}/-/archive/{tag}/{repo}-{tag}.tar.gz"
SYM_VAR, FP_VAR = "OHMAI_KICAD_SYMBOLS", "OHMAI_KICAD_FOOTPRINTS"
TAG_ENV = "OHMAI_KICAD_LIBS_TAG"   # candidate tag for `ohmai upgrade kicad-libs` / the canary (overrides ohmai.yaml)


def pinned(project: Project) -> str | None:
    if os.environ.get(TAG_ENV):
        return os.environ[TAG_ENV]
    k = project.config.get("kicad") or {}
    v = k.get("libraries") if isinstance(k, dict) else None
    return str(v) if v else None


def base_dir() -> Path:
    return Path(os.environ.get("OHMAI_KICAD_LIBS") or "~/.cache/ohmai/kicad-libs").expanduser()


def dirs(tag: str) -> tuple[Path, Path]:
    b = base_dir()
    return b / f"kicad-symbols-{tag}", b / f"kicad-footprints-{tag}"


def activate(project: Project) -> None:
    os.environ["KICAD9_3DMODEL_DIR"] = str(project.out / "3dmodels")
    tag = pinned(project)
    if not tag:
        return
    sym, fp = dirs(tag)
    os.environ[SYM_VAR] = str(sym)
    os.environ[FP_VAR] = str(fp)


def install(tag: str) -> list[Path]:
    out = []
    base_dir().mkdir(parents=True, exist_ok=True)
    for repo, target in zip(("kicad-symbols", "kicad-footprints"), dirs(tag), strict=False):
        if target.is_dir():
            out.append(target)
            continue
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "lib.tgz"
            urllib.request.urlretrieve(URL.format(repo=repo, tag=tag), f)  # noqa: S310 (fixed https host)
            with tarfile.open(f) as t:
                t.extractall(base_dir(), filter="data")
        out.append(target)
    return out


def table_paths(project: Project) -> tuple[Path, Path]:
    hw = project.schematic.parent
    return hw / "sym-lib-table", hw / "fp-lib-table"


def local_rows(table: Path) -> str:
    """Project-local rows (${KIPRJMOD}/lib/..., vetted imports) that a table rewrite must keep."""
    if not table.is_file():
        return ""
    return "".join(ln + "\n" for ln in table.read_text(encoding="utf-8").splitlines()
                   if ln.strip().startswith("(lib ") and "${KIPRJMOD}" in ln)


def write_tables(project: Project) -> tuple[Path, Path]:
    tag = pinned(project)
    if not tag:
        raise SystemExit("ohmai kicad-libs: set kicad.libraries in ohmai.yaml first")
    sym, fp = dirs(tag)
    if not sym.is_dir() or not fp.is_dir():
        raise SystemExit(f"ohmai kicad-libs: {tag} not installed in {base_dir()} (run `ohmai kicad-libs install`)")
    st, ft = table_paths(project)
    keep_s, keep_f = local_rows(st), local_rows(ft)
    libs = sorted(p.name[:-10] for p in sym.glob("*.kicad_sym"))
    fps = sorted(p.name[:-7] for p in fp.glob("*.pretty"))
    st.write_text("(sym_lib_table\n  (version 7)\n" + "".join(
        f'  (lib (name "{n}")(type "KiCad")(uri "${{{SYM_VAR}}}/{n}.kicad_sym")(options "")(descr "KiCad {tag}"))\n'
        for n in libs) + keep_s + ")\n", encoding="utf-8")
    ft.write_text("(fp_lib_table\n  (version 7)\n" + "".join(
        f'  (lib (name "{n}")(type "KiCad")(uri "${{{FP_VAR}}}/{n}.pretty")(options "")(descr "KiCad {tag}"))\n'
        for n in fps) + keep_f + ")\n", encoding="utf-8")
    return st, ft


def status(project: Project) -> tuple[str, str, list[str]]:
    """(status, summary, details) for the kicad-libs check."""
    tag = pinned(project)
    if not tag:
        return "WARN", "no kicad.libraries pin in ohmai.yaml (library version not reproducible)", []
    sym, fp = dirs(tag)
    problems = [f"{d} missing (run `ohmai kicad-libs install`)" for d in (sym, fp) if not d.is_dir()]
    for t, var in zip(table_paths(project), (SYM_VAR, FP_VAR), strict=False):
        if not t.is_file():
            problems.append(f"{t.relative_to(project.root)} missing (run `ohmai kicad-libs tables`)")
        elif f"${{{var}}}" not in t.read_text(encoding="utf-8"):
            problems.append(f"{t.relative_to(project.root)} does not use ${{{var}}}")
    if problems:
        return "FAIL", f"KiCad libraries {tag}: {len(problems)} problem(s)", problems
    return "PASS", f"KiCad libraries {tag} installed, project tables point at them", []
