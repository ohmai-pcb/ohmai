# SPDX-License-Identifier: Apache-2.0
"""Release-level tool pins shipped with this ohmai version.

Distinct from a project's toolchain.lock.yaml (what that design was built with):
this file is what *this ohmai release* was tested with. `ohmai setup` installs
exactly these versions; `ohmai doctor` FAILs on a mismatch. Developers testing a
newer tool can set OHMAI_ALLOW_UNPINNED=1 (or toolchain.allow_unpinned: true).
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from ohmai.versions import first_version, version_in_range

SCHEMA = "ohmai-pinned-tools/v1"
MANIFEST_NAME = "pinned-tools.yaml"
OVERRIDE_ENV = "OHMAI_ALLOW_UNPINNED"
PINNED_TOOLS_ENV = "OHMAI_PINNED_TOOLS"
FIX_SETUP = "ohmai setup"
FIX_UNPINNED = f"Or {OVERRIDE_ENV}=1 to test this version"

# Tool names as shown by `ohmai doctor`.
KICAD = "kicad-cli"
FREEROUTING = "freerouting"
JDK = "jdk"
EASYEDA = "easyeda2kicad"


@dataclass(frozen=True)
class PinResult:
    name: str
    status: str  # PASS / FAIL / WARN
    expected: str
    found: str | None
    message: str


# ---------------------------------------------------------------- paths
def cache_root() -> Path:
    if os.environ.get("OHMAI_CACHE"):
        return Path(os.environ["OHMAI_CACHE"]).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg).expanduser() / "ohmai"
    return Path.home() / ".cache/ohmai"


def manifest_path() -> Path:
    env = os.environ.get(PINNED_TOOLS_ENV)
    cands = [Path(env)] if env else []
    pkg = Path(__file__).resolve().parent
    cands.extend((
        pkg.parents[2] / MANIFEST_NAME,   # editable / repo checkout
        pkg / "_data" / MANIFEST_NAME,    # wheel
        pkg / MANIFEST_NAME,
    ))
    for p in cands:
        if p.is_file():
            return p
    raise FileNotFoundError(
        f"{MANIFEST_NAME} not found (looked in {', '.join(map(str, cands))}). "
        f"Set {PINNED_TOOLS_ENV} or reinstall ohmai."
    )


def load(path: Path | None = None) -> dict:
    p = path or manifest_path()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict) or not isinstance(data.get("tools"), dict):
        raise ValueError(f"{p}: not a pinned-tools manifest")
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{p}: schema {data.get('schema')!r}, expected {SCHEMA!r}")
    return data


def tool_pin(name: str, data: dict | None = None) -> dict:
    """Canonical manifest key (kicad, freerouting, jdk, easyeda2kicad) -> pin dict."""
    key = {"kicad-cli": "kicad", "kicad": "kicad"}.get(name, name)
    tools = (data or load())["tools"]
    pin = tools.get(key)
    if not isinstance(pin, dict) or not pin.get("version"):
        raise KeyError(f"pinned-tools.yaml: missing tools.{key}.version")
    return pin


# ---------------------------------------------------------------- version compare
def _truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in {"1", "true", "yes", "on"}


def allow_unpinned(cwd: Path | None = None, override: bool | None = None) -> bool:
    if override is not None:
        return override
    if _truthy(os.environ.get(OVERRIDE_ENV)):
        return True
    if cwd is not None:
        try:
            from ohmai.project import find_project
            cfg = (find_project(cwd).config.get("toolchain") or {})
            if isinstance(cfg, dict) and cfg.get("allow_unpinned"):
                return True
        except Exception:
            return False
    return False


def expected_label(pin: dict) -> str:
    ver = str(pin["version"])
    if pin.get("match") == "major":
        return f"{ver} (major)"
    rng = pin.get("range")
    return f"{ver} (range {rng})" if rng and rng != ver else ver


def version_matches(found: str, pin: dict) -> bool:
    if pin.get("match") == "major":
        nums = re.findall(r"\d+", found or "")
        want = re.findall(r"\d+", str(pin["version"]))
        return bool(nums and want and nums[0] == want[0])
    spec = str(pin.get("range") or pin["version"])
    if not re.search(r"[<>=!*x]", spec):
        spec = f"=={spec}"
    ok, err = version_in_range(found, spec)
    return bool(ok and not err)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_list(pin: dict) -> list[str]:
    raw = pin.get("sha256")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.lower()]
    return [str(s).lower() for s in raw]


# ---------------------------------------------------------------- detection
def detect_kicad() -> tuple[str | None, str | None]:
    from ohmai import kicad
    exe = kicad.kicad_cli()
    ver = kicad.version() if exe else None
    return ver, exe


def detect_easyeda() -> tuple[str | None, str | None]:
    try:
        return md.version("easyeda2kicad"), "easyeda2kicad"
    except md.PackageNotFoundError:
        return None, None


def freerouting_cache_jar(pin: dict | None = None) -> Path:
    pin = pin or tool_pin("freerouting")
    return cache_root() / "freerouting" / str(pin.get("filename") or f"freerouting-{pin['version']}.jar")


def _jar_version(path: Path) -> str | None:
    import zipfile
    if path.suffix == ".jar":
        try:
            with zipfile.ZipFile(path) as z:
                mf = z.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
            m = re.search(r"^Implementation-Version:\s*(\S+)", mf, re.M)
            v = first_version(m.group(1)) if m else None
            if v:
                return v
        except (OSError, KeyError, zipfile.BadZipFile):
            pass
    return first_version(path.name)


def detect_freerouting(pin: dict | None = None) -> tuple[str | None, Path | None, str | None]:
    """(version, path, sha256 or None). Prefers env, then the pinned cache path."""
    pin = pin or tool_pin("freerouting")
    cands: list[Path] = []
    for key in ("OHMAI_FREEROUTING_JAR", "FREEROUTING_JAR"):
        env = os.environ.get(key)
        if env:
            cands.append(Path(env).expanduser())
    cands.append(freerouting_cache_jar(pin))
    exe = shutil.which("freerouting")
    if exe:
        cands.append(Path(exe))
    seen: set[Path] = set()
    for path in cands:
        try:
            path = path.resolve()
        except OSError:
            continue
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        ver = _jar_version(path)
        digest = file_sha256(path) if path.suffix == ".jar" else None
        return ver, path, digest
    return None, None, None


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share").expanduser()


def freerouting_user_data_path(version: str | None = None) -> Path:
    """OhmAI-owned Freerouting userdata dir for this pin (never ~/.config/freerouting)."""
    if not version:
        try:
            version = str(tool_pin("freerouting")["version"])
        except (FileNotFoundError, KeyError, ValueError):
            version = "default"
    return data_home() / "ohmai" / "freerouting" / version / "userdata"


def java_version(exe: str | Path) -> str | None:
    """Version reported by `java -version` for a binary or JAVA_HOME-style directory."""
    path = Path(exe).expanduser()
    if path.is_dir():
        path = path / "bin" / "java" if (path / "bin" / "java").is_file() else path / "java"
    if not path.is_file():
        return None
    try:
        r = subprocess.run([str(path), "-version"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'version "([^"]+)"', r.stderr + r.stdout)
    return m.group(1) if m and r.returncode == 0 else None


def resolve_java(exe: str | Path) -> Path | None:
    path = Path(exe).expanduser()
    if path.is_dir():
        path = path / "bin" / "java" if (path / "bin" / "java").is_file() else path / "java"
    return path.resolve() if path.is_file() else None


def jdk_search_roots() -> list[Path]:
    """Well-known places a JDK 25 may already be installed (commissioning or this box)."""
    data = data_home()
    roots = [
        cache_root() / "jdk",
        cache_root() / "jdk25",
        data / "ohmai" / "jdk",
        data / "ohmai" / "jdk25",
        data / "jdk25",
        Path.home() / ".local/share/jdk25",
        Path("/workspace/.toolcache/jdk25"),
    ]
    seen: set[Path] = set()
    out: list[Path] = []
    for r in roots:
        try:
            r = r.expanduser()
        except OSError:
            continue
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def _java_bins_under(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root.resolve()] if root.name == "java" else []
    found: list[Path] = []
    direct = root / "bin" / "java"
    if direct.is_file():
        found.append(direct.resolve())
    try:
        for p in sorted(root.glob("**/bin/java")):
            if p.is_file():
                rp = p.resolve()
                if rp not in found:
                    found.append(rp)
    except OSError:
        pass
    return found


def discover_jdk(*, require_major: str | None = "25") -> tuple[str | None, str | None]:
    """Locate a Java binary. FREEROUTING_JAVA / OHMAI_FREEROUTING_JAVA win as an override
    (even when the major does not match). Otherwise prefer a well-known JDK 25, then
    JAVA_HOME / PATH if they satisfy require_major."""
    for key in ("FREEROUTING_JAVA", "OHMAI_FREEROUTING_JAVA"):
        env = os.environ.get(key)
        if not env:
            continue
        path = resolve_java(env)
        if path:
            return java_version(path), str(path)
        # env set but unusable: keep looking (same as a missing path)
    found_other: tuple[str, str] | None = None

    def consider(exe: Path) -> tuple[str, str] | None:
        nonlocal found_other
        ver = java_version(exe)
        if not ver:
            return None
        loc = str(exe)
        if require_major is None or version_matches(ver, {"version": require_major, "match": "major"}):
            return ver, loc
        if found_other is None:
            found_other = (ver, loc)
        return None

    for root in jdk_search_roots():
        for exe in _java_bins_under(root):
            hit = consider(exe)
            if hit:
                return hit
    home = os.environ.get("JAVA_HOME")
    if home:
        path = resolve_java(Path(home))
        if path:
            hit = consider(path)
            if hit:
                return hit
    which = shutil.which("java")
    if which:
        path = resolve_java(which)
        if path:
            hit = consider(path)
            if hit:
                return hit
    if found_other:
        return found_other
    return None, None


def detect_jdk() -> tuple[str | None, str | None]:
    """(version, java binary). Env override, then auto-discovered JDK 25, then any other java."""
    try:
        major = str(tool_pin("jdk")["version"])
    except (FileNotFoundError, KeyError, ValueError):
        major = "25"
    return discover_jdk(require_major=major)


# ---------------------------------------------------------------- check
def _fix(name: str, pin: dict) -> str:
    if name == KICAD:
        ver = pin["version"]
        return (f"install KiCad {ver} (kicad-cli on PATH or KICAD_CLI), then {FIX_SETUP}. "
                f"{FIX_UNPINNED}")
    if name == FREEROUTING:
        return f"{FIX_SETUP} (fetches the pinned jar and verifies sha256). {FIX_UNPINNED}"
    if name == JDK:
        return (f"install JDK {pin['version']} and set FREEROUTING_JAVA to its java binary, "
                f"then {FIX_SETUP}. {FIX_UNPINNED}")
    if name == EASYEDA:
        return f"{FIX_SETUP} (pip install {pin.get('pypi', 'easyeda2kicad')}=={pin['version']}). {FIX_UNPINNED}"
    return f"{FIX_SETUP}. {FIX_UNPINNED}"


def _result(name: str, pin: dict, found: str | None, ok: bool, detail: str = "",
            allow: bool = False) -> PinResult:
    expected = expected_label(pin)
    if ok:
        msg = f"{name} {found} matches pin {expected}"
        return PinResult(name, "PASS", expected, found, msg)
    shown = found or "not found"
    msg = f"expected {expected}, found {shown}"
    if detail:
        msg += f" ({detail})"
    msg += f". Fix: {_fix(name, pin)}"
    if allow:
        return PinResult(name, "WARN", expected, found, msg + f" [{OVERRIDE_ENV}=1]")
    return PinResult(name, "FAIL", expected, found, msg)


def check_one(name: str, pin: dict, found_ver: str | None, *, digest: str | None = None,
              allow: bool = False) -> PinResult:
    if found_ver is None:
        return _result(name, pin, None, False, allow=allow)
    if not version_matches(found_ver, pin):
        return _result(name, pin, found_ver, False, allow=allow)
    want = _sha256_list(pin)
    if want and digest and digest.lower() not in want:
        return _result(name, pin, found_ver, False, detail="sha256 mismatch", allow=allow)
    return _result(name, pin, found_ver, True, allow=allow)


def check_all(cwd: Path | None = None, *, data: dict | None = None,
              found: dict | None = None, allow_unpinned_flag: bool | None = None) -> list[PinResult]:
    """Check every pinned tool. `found` maps doctor-name -> version (tests) or
    a dict with optional keys version/sha256."""
    data = data or load()
    allow = allow_unpinned(cwd, allow_unpinned_flag)
    out: list[PinResult] = []

    def override(name: str) -> tuple[str | None, str | None] | None:
        if not found or name not in found:
            return None
        raw = found[name]
        if raw is None:
            return None, None
        if isinstance(raw, str):
            return raw, None
        return raw.get("version"), raw.get("sha256")

    pin = tool_pin("kicad", data)
    ov = override(KICAD)
    ver = ov[0] if ov else detect_kicad()[0]
    out.append(check_one(KICAD, pin, ver, allow=allow))

    pin = tool_pin("freerouting", data)
    ov = override(FREEROUTING)
    if ov:
        ver, digest = ov
    else:
        ver, _path, digest = detect_freerouting(pin)
    out.append(check_one(FREEROUTING, pin, ver, digest=digest, allow=allow))

    pin = tool_pin("jdk", data)
    ov = override(JDK)
    ver = ov[0] if ov else detect_jdk()[0]
    out.append(check_one(JDK, pin, ver, allow=allow))

    pin = tool_pin("easyeda2kicad", data)
    ov = override(EASYEDA)
    ver = ov[0] if ov else detect_easyeda()[0]
    out.append(check_one(EASYEDA, pin, ver, allow=allow))
    return out


# ---------------------------------------------------------------- install
def backup_if_overwrite(path: Path) -> Path | None:
    """Copy `path` to path.name.YYYY-MM-DD_HHMM.bak before replacing a file outside git."""
    if not path.is_file():
        return None
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    bak = path.with_name(f"{path.name}.{ts}.bak")
    n = 1
    while bak.exists():
        bak = path.with_name(f"{path.name}.{ts}-{n}.bak")
        n += 1
    shutil.copy2(path, bak)
    return bak


def _download(url: str, dest: Path, sha256s: list[str], timeout: int = 120) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dest.name + ".", suffix=".part", dir=dest.parent)
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, open(tmp_path, "wb") as out:
            shutil.copyfileobj(resp, out)
        digest = file_sha256(tmp_path)
        if sha256s and digest.lower() not in sha256s:
            raise ValueError(f"sha256 mismatch for {url}: got {digest}, expected one of {sha256s}")
        if dest.is_file() and file_sha256(dest).lower() == digest.lower():
            tmp_path.unlink(missing_ok=True)
            return
        if dest.is_file():
            backup_if_overwrite(dest)
        tmp_path.replace(dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def install_freerouting(pin: dict | None = None) -> str:
    pin = pin or tool_pin("freerouting")
    dest = freerouting_cache_jar(pin)
    want = _sha256_list(pin)
    if dest.is_file() and want and file_sha256(dest).lower() in want:
        return f"freerouting {pin['version']}  {dest}  sha256 ok (already present)"
    url = pin.get("url")
    if not url:
        raise ValueError("pinned-tools.yaml: tools.freerouting.url is missing")
    _download(str(url), dest, want)
    return f"freerouting {pin['version']}  {dest}  sha256 ok"


def install_easyeda(pin: dict | None = None, *, pip: list[str] | None = None) -> str:
    pin = pin or tool_pin("easyeda2kicad")
    ver = str(pin["version"])
    name = str(pin.get("pypi") or "easyeda2kicad")
    have, _ = detect_easyeda()
    if have == ver:
        return f"easyeda2kicad {ver}  already installed"
    hashes = _sha256_list(pin)
    cmd = list(pip or [sys.executable, "-m", "pip"])
    if hashes:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write(f"{name}=={ver}")
            for h in hashes:
                fh.write(f" --hash=sha256:{h}")
            fh.write("\n")
            req = Path(fh.name)
        try:
            r = subprocess.run(cmd + ["install", "--require-hashes", "-r", str(req)],
                               capture_output=True, text=True, timeout=180)
        finally:
            req.unlink(missing_ok=True)
    else:
        r = subprocess.run(cmd + ["install", f"{name}=={ver}"],
                           capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-400:]
        raise RuntimeError(f"pip install {name}=={ver} failed: {tail}")
    return f"easyeda2kicad {ver}  installed"


def _kicad_fix(pin: dict) -> str:
    ver = pin["version"]
    return (f"install KiCad {ver} (Debian/Ubuntu: apt-get install kicad={ver}* ; "
            f"otherwise the official KiCad {ver} installer) and put kicad-cli on PATH or set KICAD_CLI")


def install_all(*, data: dict | None = None, download: bool = True) -> tuple[list[str], list[str]]:
    """Install fetchable pins and report the others. Returns (notices, errors).

    No prompts. Freerouting jar and easyeda2kicad are fetched; KiCad and JDK are
    verified in place (they need a system/package install) and get a one-line
    notice or a fix command.
    """
    data = data or load()
    notices, errors = [], []

    pin = tool_pin("kicad", data)
    ver, exe = detect_kicad()
    if ver and version_matches(ver, pin):
        notices.append(f"kicad {ver}  {exe or 'kicad-cli'}")
    else:
        errors.append(f"kicad: expected {expected_label(pin)}, found {ver or 'not found'}. {_kicad_fix(pin)}")

    pin = tool_pin("freerouting", data)
    try:
        if download:
            notices.append(install_freerouting(pin))
        else:
            ver, path, digest = detect_freerouting(pin)
            want = _sha256_list(pin)
            ok = ver and version_matches(ver, pin) and (not want or (digest or "").lower() in want)
            if ok:
                notices.append(f"freerouting {ver}  {path}  sha256 ok")
            else:
                errors.append(f"freerouting: expected {expected_label(pin)}, found {ver or 'not found'}. {FIX_SETUP}")
    except (OSError, ValueError, urllib.error.URLError) as e:
        errors.append(f"freerouting: failed to install {pin['version']}: {e}")

    pin = tool_pin("jdk", data)
    ver, exe = detect_jdk()
    if ver and version_matches(ver, pin):
        notices.append(f"jdk {ver}  {exe}")
    else:
        errors.append(
            f"jdk: expected {expected_label(pin)}, found {ver or 'not found'}. "
            f"install JDK {pin['version']} and set FREEROUTING_JAVA to its java binary"
        )

    pin = tool_pin("easyeda2kicad", data)
    try:
        if download:
            notices.append(install_easyeda(pin))
        else:
            ver, _ = detect_easyeda()
            if ver and version_matches(ver, pin):
                notices.append(f"easyeda2kicad {ver}  already installed")
            else:
                errors.append(
                    f"easyeda2kicad: expected {expected_label(pin)}, found {ver or 'not found'}. {FIX_SETUP}"
                )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as e:
        errors.append(f"easyeda2kicad: failed to install {pin['version']}: {e}")

    return notices, errors
