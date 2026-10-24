"""Toolchain version lock: `toolchain.lock.yaml` per project, `ohmai lock`, `ohmai check toolchain`.

Two tiers (owner decision 2026-10-15):

  tier 1  design-affecting, exact pins: the KiCad application (kicad-cli), the official KiCad libraries,
          Freerouting, KiKit, KiBot, InteractiveHtmlBom, kicad-python (kipy), SKiDL, jlcparts, and the
          Python packages ohmai runs on. GPL/AGPL tools (Freerouting, KiBot) only ever run as separate
          processes; ohmai reads their version, it never imports them.
          `ohmai check toolchain` FAILs when the installed version differs from the lock (or a locked tool
          is missing, or a portable content hash differs).
  tier 2  helpers (Python interpreter, git, rsvg-convert, ohmai itself): a version range; WARN outside it.

The lock records what `ohmai lock` found: version, and a sha256 where one can be computed (library tree,
jar, the Python package's RECORD, the kicad-cli binary). Hashes are `portable` (same bytes on every
machine: library tarballs, jars) or `machine` (a distro build of kicad-cli, a platform wheel): a portable
mismatch FAILs, a machine mismatch only WARNs. A tier-1 tool the project does not use is locked as
`version: null` (absent); installing it later changes nothing until `ohmai upgrade` adds it.

ohmai.yaml `kicad.app` stays the policy range for the KiCad application and `kicad.libraries` the library
tag; the lock pins the exact build inside them (`kicad-app` and `kicad-libs` checks read both). Moving a
tier-1 pin goes through `ohmai upgrade <tool> <version>` (golden + known-bad suites first), never by hand.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from ohmai import __version__, kicad, kicadlibs
from ohmai.project import Project
from ohmai.versions import first_version, version_in_range

LOCK_NAME = "toolchain.lock.yaml"
SCHEMA = "ohmai-toolchain-lock/v1"


# ---------------------------------------------------------------- hashing
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cache_file() -> Path:
    return Path(os.environ.get("OHMAI_CACHE") or "~/.cache/ohmai").expanduser() / "tree-hash.json"


def tree_sha256(dirs: list[Path]) -> tuple[str, int]:
    """Content hash of directory trees (relative path + file sha256, sorted), and the file count.
    Cached by a stat manifest (path, size, mtime) so repeated checks do not re-read 400 MB of libraries."""
    files: list[tuple[str, Path]] = []
    for d in dirs:
        for f in sorted(d.rglob("*")):
            if f.is_file() and ".git" not in f.relative_to(d).parts:
                files.append((f"{d.name}/{f.relative_to(d).as_posix()}", f))
    files.sort()
    st = hashlib.sha256()
    for rel, f in files:
        s = f.stat()
        st.update(f"{rel}\0{s.st_size}\0{s.st_mtime_ns}\n".encode())
    key = st.hexdigest()
    cache_f = _cache_file()
    try:
        cache = json.loads(cache_f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if key in cache:
        return cache[key], len(files)
    h = hashlib.sha256()
    for rel, f in files:
        h.update(f"{rel}\0{file_sha256(f)}\n".encode())
    digest = h.hexdigest()
    try:
        cache_f.parent.mkdir(parents=True, exist_ok=True)
        cache = {k: v for k, v in list(cache.items())[-50:]}
        cache[key] = digest
        cache_f.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
    return digest, len(files)


# ---------------------------------------------------------------- detection
Found = dict  # {"version": str, "sha256"?: str, "hash_scope"?: "portable"|"machine", "path"?: str, "files"?: int}


def _run_version(cmd: list[str]) -> str | None:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return first_version(r.stdout + "\n" + r.stderr) if r.returncode == 0 else None


def _dist(name: str) -> Found | None:
    try:
        d = md.distribution(name)
    except md.PackageNotFoundError:
        return None
    out: Found = {"version": d.version}
    record = d.read_text("RECORD")
    if record:
        out["sha256"] = hashlib.sha256(record.encode()).hexdigest()
        out["hash_scope"] = "machine"   # the installed wheel's RECORD: platform wheels differ per machine
    return out


def detect_kicad_app(project: Project) -> Found | None:
    exe = kicad.kicad_cli()
    if not exe:
        return None
    v = kicad.version()
    if not v:
        return None
    real = Path(shutil.which(exe) or exe).resolve()
    out: Found = {"version": v, "path": str(real)}
    if real.is_file():
        out.update(sha256=file_sha256(real), hash_scope="machine")
    return out


def detect_kicad_libs(project: Project) -> Found | None:
    tag = kicadlibs.pinned(project)
    if not tag:
        return None
    sym, fp = kicadlibs.dirs(tag)
    if not (sym.is_dir() and fp.is_dir()):
        return None
    digest, n = tree_sha256([sym.resolve(), fp.resolve()])
    return {"version": tag, "sha256": digest, "hash_scope": "portable", "files": n}


def detect_freerouting(project: Project) -> Found | None:
    jar = os.environ.get("FREEROUTING_JAR")
    path = Path(jar) if jar else None
    if path is None:
        exe = shutil.which("freerouting")
        if not exe:
            return None
        path = Path(exe).resolve()
    if not path.is_file():
        return None
    v = None
    if path.suffix == ".jar":
        try:
            with zipfile.ZipFile(path) as z:
                mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
            m = re.search(r"^Implementation-Version:\s*(\S+)", mf, re.M)
            # release jars (2.1.0 ... 2.5.0) say "Implementation-Version: unspecified": use the file name then
            v = first_version(m.group(1)) if m else None
        except (OSError, KeyError, zipfile.BadZipFile):
            v = None
    v = v or first_version(path.name) or _run_version([str(path), "--version"])
    if not v:
        return None
    found: Found = {"version": v, "sha256": file_sha256(path), "hash_scope": "portable", "path": str(path)}
    if path.suffix == ".jar":
        java = freerouting_java()
        if java:
            found["java"] = java
    return found


def freerouting_java() -> str | None:
    """Version of the Java runtime that runs the Freerouting jar: FREEROUTING_JAVA (path to `java`) or `java` on
    PATH. Part of the tier-1 pin: Freerouting >= 2.2 needs Java 25 and routes differently per runtime build."""
    exe = os.environ.get("FREEROUTING_JAVA") or shutil.which("java")
    if not exe or not Path(exe).is_file():
        return None
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'version "([^"]+)"', r.stderr + r.stdout)
    return m.group(1) if (r.returncode == 0 and m) else None


def _py_or_cli(dist: str, cli: list[str]) -> Callable[[Project], Found | None]:
    def detect(project: Project) -> Found | None:
        return _dist(dist) or ({"version": v} if (v := _run_version(cli)) else None)
    return detect


def detect_ibom(project: Project) -> Found | None:
    found = _dist("InteractiveHtmlBom")
    if found:
        return found
    d = os.environ.get("OHMAI_IBOM_DIR")
    vf = Path(d) / "InteractiveHtmlBom" / "version.py" if d else None
    if vf and vf.is_file():
        m = re.search(r"LAST_TAG\s*=\s*['\"]v?([\d.]+)", vf.read_text(encoding="utf-8"))
        if m:
            return {"version": m.group(1), "sha256": tree_sha256([vf.parent])[0], "hash_scope": "portable",
                    "path": str(vf.parent)}
    return None


def _jlcparts_db() -> Found | None:
    """The jlcparts database file (OHMAI_JLCPARTS_DB): version = its mtime date, hash of the file."""
    db = os.environ.get("OHMAI_JLCPARTS_DB")
    p = Path(db) if db else None
    if not p or not p.is_file():
        return None
    v = date.fromtimestamp(p.stat().st_mtime).isoformat()
    return {"version": f"db-{v}", "sha256": file_sha256(p), "hash_scope": "portable", "path": str(p)}


def python_deps() -> list[str]:
    """Runtime dependency closure of the ohmai package (installed distributions; extras excluded)."""
    seen: dict[str, str] = {}
    todo = ["ohmai"]
    while todo:
        name = todo.pop()
        try:
            reqs = md.requires(name) or []
        except md.PackageNotFoundError:
            continue
        for r in reqs:
            if "extra ==" in r.replace("extra==", "extra =="):
                continue
            m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", r)
            if not m:
                continue
            try:
                dist = md.distribution(m.group(1))
            except md.PackageNotFoundError:
                continue   # marker excluded it on this interpreter (or it is missing: pip check's job)
            key = dist.metadata["Name"]
            if key.lower() not in {k.lower() for k in seen}:
                seen[key] = dist.version
                todo.append(key)
    return sorted(seen, key=str.lower)


@dataclass(frozen=True)
class Tool:
    name: str
    tier: int
    title: str
    detect: Callable[[Project], Found | None]
    default_range: str | None = None
    install_hint: str = ""


def _python(project: Project) -> Found:
    return {"version": ".".join(map(str, sys.version_info[:3]))}


def _ohmai(project: Project) -> Found:
    return {"version": __version__}


def _cli(cmd: list[str]) -> Callable[[Project], Found | None]:
    return lambda project: ({"version": v} if (v := _run_version(cmd)) else None)


def _major_minor(v: str) -> str:
    return ".".join(re.findall(r"\d+", v)[:2])


TOOLS: tuple[Tool, ...] = (
    Tool("kicad-app", 1, "KiCad application (kicad-cli)", detect_kicad_app,
         install_hint="install that KiCad build or set KICAD_CLI to it"),
    Tool("kicad-libs", 1, "Official KiCad symbol + footprint libraries", detect_kicad_libs,
         install_hint="ohmai kicad-libs install"),
    Tool("freerouting", 1, "Freerouting autorouter", detect_freerouting,
         install_hint="set FREEROUTING_JAR to the jar (or put `freerouting` on PATH)"),
    Tool("kikit", 1, "KiKit (panels, fab presets)", _py_or_cli("KiKit", ["kikit", "--version"]),
         install_hint="pip install KiKit==<version>"),
    Tool("kibot", 1, "KiBot (fab plugins, release outputs)", _py_or_cli("kibot", ["kibot", "--version"]),
         install_hint="pip install kibot==<version>"),
    Tool("ibom", 1, "InteractiveHtmlBom", detect_ibom,
         install_hint="pip install InteractiveHtmlBom==<version> or set OHMAI_IBOM_DIR"),
    Tool("kicad-python", 1, "kicad-python (kipy, KiCad IPC API client)", lambda project: _dist("kicad-python"),
         install_hint="pip install kicad-python==<version>"),
    Tool("skidl", 1, "SKiDL (independent netlist cross-check, `skidl-netlist`)", lambda project: _dist("skidl"),
         install_hint="pip install skidl==<version>"),
    Tool("jlcparts", 1, "jlcparts (offline JLC/LCSC parametric parts database, `ohmai parts search`)",
         lambda project: _dist("jlcparts") or _jlcparts_db(),
         install_hint="pip install jlcparts==<version>, or set OHMAI_JLCPARTS_DB to the downloaded cache.sqlite3"),
    # tier 2 helpers
    Tool("python", 2, "Python interpreter", _python, ">=3.10"),
    Tool("ohmai", 2, "OhmAI CLI", _ohmai),
    Tool("git", 2, "git", _cli(["git", "--version"]), ">=2.30"),
    Tool("rsvg-convert", 2, "rsvg-convert (render PNG)", _cli(["rsvg-convert", "--version"]), ">=2.40"),
)
BY_NAME = {t.name: t for t in TOOLS}
PY_PREFIX = "python:"


def tool(name: str) -> Tool | None:
    if name in BY_NAME:
        return BY_NAME[name]
    if name.startswith(PY_PREFIX):
        pkg = name[len(PY_PREFIX):]
        return Tool(name, 1, f"Python package {pkg} (ohmai dependency)", lambda project, pkg=pkg: _dist(pkg),
                    install_hint=f"pip install {pkg}==<version> (or pip install -c cli/constraints.txt)")
    return None


def tool_names(lock: dict | None = None) -> list[str]:
    names = [t.name for t in TOOLS if t.tier == 1] + [PY_PREFIX + p for p in python_deps()] \
        + [t.name for t in TOOLS if t.tier == 2]
    for n in (lock or {}).get("tools") or {}:
        if n not in names and tool(n) is not None:
            names.append(n)
    return names


def detect(project: Project, name: str, opts: dict | None = None) -> Found | None:
    """Installed version of a tool. opts['toolchain'] = {name: Found|None} overrides detection (tests)."""
    over = (opts or {}).get("toolchain") or {}
    if name in over:
        return over[name]
    t = tool(name)
    return t.detect(project) if t else None


# ---------------------------------------------------------------- lock file
def lock_path(project: Project) -> Path:
    rel = (project.config.get("toolchain") or {}).get("lock") if isinstance(project.config.get("toolchain"), dict) \
        else None
    return project.root / (rel or LOCK_NAME)


def load_lock(project: Project) -> dict | None:
    p = lock_path(project)
    if not p.is_file():
        return None
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


HEADER = """\
# OhmAI toolchain lock (generated by `ohmai lock`; verified by `ohmai check toolchain`).
# Tier 1 = design-affecting, exact pins: never edit by hand, move a pin with `ohmai upgrade <tool> <version>`
# (runs the golden + known-bad suites first and commits the lock on its own). version: null = not used.
# Tier 2 = helpers: `range` may be edited (owner decision); `seen` is what `ohmai lock` found.
"""


def write_lock(project: Project, data: dict) -> Path:
    p = lock_path(project)
    p.write_text(HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")
    return p


def entry_for(project: Project, name: str, found: Found | None, old: dict | None = None) -> dict:
    t = tool(name)
    assert t is not None
    if t.tier == 2:
        rng = (old or {}).get("range") or t.default_range
        if rng is None and found:
            rng = f"=={_major_minor(found['version'])}.x" if name == "ohmai" else f">={found['version']}"
        e = {"tier": 2, "range": rng, "seen": found["version"] if found else None}
        return e
    e = {"tier": 1, "version": found["version"] if found else None}
    for k in ("sha256", "hash_scope", "files", "java"):
        if found and k in found:
            e[k] = found[k]
    if found and "path" in found:
        e["seen_at"] = found["path"]
    return e


def make_lock(project: Project, opts: dict | None = None) -> tuple[dict, list[str], list[str]]:
    """(new lock, tier-1 changes vs the existing lock, problems that block writing it)."""
    old = load_lock(project) or {}
    old_tools = old.get("tools") or {}
    tools, changes, problems = {}, [], []
    for name in tool_names(old):
        found = detect(project, name, opts)
        new = entry_for(project, name, found, old_tools.get(name))
        prev = old_tools.get(name)
        if new["tier"] == 1 and prev is not None and str(prev.get("version")) != str(new.get("version")):
            changes.append(f"{name}: lock {prev.get('version')} -> installed {new.get('version')}")
        elif new["tier"] == 1 and prev is not None and prev.get("sha256") and new.get("sha256") \
                and prev["sha256"] != new["sha256"] and new.get("hash_scope") == "portable":
            changes.append(f"{name} {new['version']}: content hash changed (same version, different files)")
        tools[name] = new
    app = tools.get("kicad-app", {}).get("version")
    spec = (project.config.get("kicad") or {}).get("app")
    if app and spec:
        ok, err = version_in_range(app, spec)
        if err or not ok:
            problems.append(f"kicad-cli {app} is outside ohmai.yaml kicad.app {spec!r}{': ' + err if err else ''}")
    tag = kicadlibs.pinned(project)
    if tag and not tools.get("kicad-libs", {}).get("version"):
        problems.append(f"KiCad libraries {tag} (ohmai.yaml kicad.libraries) not installed: ohmai kicad-libs install")
    data = {"schema": SCHEMA, "generated": {"date": date.today().isoformat(), "by": f"ohmai lock ({__version__})"},
            "tools": tools}
    if old.get("history"):
        data["history"] = old["history"]
    return data, changes, problems


# ---------------------------------------------------------------- verification
def verify_entry(name: str, want: dict, found: Found | None) -> tuple[str, str]:
    """(status, message) for one lock entry vs what is installed. status: PASS/FAIL/WARN."""
    t = tool(name)
    title = t.title if t else name
    if want.get("tier") == 2:
        rng = want.get("range")
        if found is None:
            return "WARN", f"{name}: not found ({title}; range {rng})"
        if not rng:
            return "PASS", f"{name} {found['version']} (no range)"
        ok, err = version_in_range(found["version"], str(rng))
        if err:
            return "FAIL", f"{name}: lock range {rng!r}: {err}"
        return ("PASS", f"{name} {found['version']} in {rng}") if ok else \
            ("WARN", f"{name} {found['version']} outside the helper range {rng!r}")
    v = want.get("version")
    hint = f" ({t.install_hint})" if t and t.install_hint else ""
    if v is None:
        return "PASS", f"{name}: not used (locked absent)" + (f"; {found['version']} installed, ignored" if found else "")
    if found is None:
        return "FAIL", f"{name}: locked {v}, not installed{hint}"
    if str(found["version"]) != str(v):
        return "FAIL", (f"{name}: installed {found['version']} != locked {v} (tier 1, exact){hint}; "
                        f"to move the pin: ohmai upgrade {name} {found['version']}")
    if want.get("java") and str(found.get("java")) != str(want["java"]):
        return "FAIL", (f"{name} {v}: runs on Java {found.get('java') or '(not found)'} != locked Java {want['java']} "
                        "(tier 1; set FREEROUTING_JAVA to the locked runtime or `ohmai upgrade`)")
    if want.get("sha256") and found.get("sha256") and want["sha256"] != found["sha256"]:
        if want.get("hash_scope", found.get("hash_scope")) == "portable":
            return "FAIL", f"{name} {v}: content hash differs from the lock (same version, different files: " \
                           f"modified or corrupt install?)"
        return "WARN", f"{name} {v}: different build than the locked one (machine-specific hash differs)"
    return "PASS", f"{name} {v} = lock"


def verify(project: Project, opts: dict | None = None, names: list[str] | None = None):
    """[(status, message)] for every lock entry (or only `names`); None when there is no lock."""
    lock = load_lock(project)
    if lock is None:
        return None
    tools = lock.get("tools") or {}
    out = []
    for name, want in tools.items():
        if names is not None and name not in names:
            continue
        if not isinstance(want, dict) or tool(name) is None:
            out.append(("FAIL", f"{name}: unknown tool or malformed entry in {LOCK_NAME}"))
            continue
        out.append(verify_entry(name, want, detect(project, name, opts)))
    if names is None:
        for name in tool_names(lock):
            if name not in tools and tool(name).tier == 1:
                out.append(("WARN", f"{name}: not in the lock (new dependency?); run `ohmai lock`"))
    return out


def consistency(project: Project) -> list[str]:
    """Lock vs ohmai.yaml policy: kicad-app inside kicad.app, kicad-libs == kicad.libraries."""
    lock = load_lock(project) or {}
    tools = lock.get("tools") or {}
    probs = []
    app = (tools.get("kicad-app") or {}).get("version")
    spec = (project.config.get("kicad") or {}).get("app")
    if app and spec:
        ok, err = version_in_range(str(app), str(spec))
        if err or not ok:
            probs.append(f"lock pins kicad-app {app}, outside ohmai.yaml kicad.app {spec!r}")
    libs = (tools.get("kicad-libs") or {}).get("version")
    tag = kicadlibs.pinned(project)
    if "kicad-libs" in tools and str(libs) != str(tag) and not (libs is None and tag is None):
        probs.append(f"lock pins kicad-libs {libs}, ohmai.yaml kicad.libraries is {tag}")
    if lock and lock.get("schema") != SCHEMA:
        probs.append(f"{LOCK_NAME}: schema {lock.get('schema')!r}, expected {SCHEMA!r}")
    return probs
