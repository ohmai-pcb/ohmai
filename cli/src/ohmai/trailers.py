"""`OhmAI-Author:` commit trailers: format, validation, the commit-msg hook, `ohmai commit`, `ohmai hooks`.

Every commit on a phase branch carries one well-formed trailer

    OhmAI-Author: name=<who>; kind=agent|human; vendor=<v>; model=<m>; instructions=<skill/prompt>

kind=agent needs all five keys non-empty. kind=human (the owner's merge commits, a person's own commit) needs
name and kind; vendor/model/instructions are optional for humans. The review gate reads author identities
from these trailers (ohmai/review.py `authors`), so a missing trailer means reviewer independence cannot be
proven. History before `commits.trailer_required_since` (ohmai.yaml) is grandfathered: WARN, not FAIL, so
existing history is never rewritten.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

KEY = "OhmAI-Author"
FIELDS = ("name", "kind", "vendor", "model", "instructions")
KINDS = ("agent", "human")
ENV = {f: f"OHMAI_AUTHOR_{f.upper()}" for f in FIELDS}
HOOK_MARK = "# ohmai commit-msg hook"


def parse(value: str) -> dict:
    out = {}
    for part in value.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def validate(value: str) -> list[str]:
    """Problems with one trailer value ('' = none)."""
    d = parse(value)
    if not d:
        return [f"'{value.strip()}' is not key=value; key=value; ... ({'; '.join(f + '=...' for f in FIELDS)})"]
    errs = []
    kind = d.get("kind", "")
    if kind not in KINDS:
        errs.append(f"kind must be agent or human, not '{kind}'" if kind else "kind= missing")
    need = FIELDS if kind != "human" else ("name", "kind")
    errs += [f"{f}= missing or empty" for f in need if f != "kind" and not d.get(f)]
    return errs


def format_trailer(ident: dict) -> str:
    return "; ".join(f"{f}={ident[f]}" for f in FIELDS if ident.get(f))


def message_trailers(msg: str) -> list[str]:
    """OhmAI-Author values from the trailer block (last paragraph) of a commit message, as git parses it."""
    try:
        res = subprocess.run(["git", "interpret-trailers", "--parse", "--no-divider"], input=msg,
                             capture_output=True, text=True, timeout=30)
        lines = res.stdout.splitlines() if res.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        lines = None
    if lines is None:   # no git: last paragraph, Key: value lines
        paras = [p for p in re.split(r"\n\s*\n", msg.strip()) if p.strip()]
        lines = paras[-1].splitlines() if paras else []
    rx = re.compile(rf"^{KEY}\s*:\s*(.*)$", re.I)
    return [m.group(1).strip() for ln in lines if (m := rx.match(ln.strip()))]


def check_message(msg: str) -> list[str]:
    body = "\n".join(ln for ln in msg.splitlines() if not ln.startswith("#"))
    vals = message_trailers(body)
    if not vals:
        return [f"no `{KEY}:` trailer in the last paragraph of the message"]
    errs = []
    for v in vals:
        errs += [f"`{KEY}: {v}`: {e}" for e in validate(v)]
    return errs


# ---------- configured identity (ohmai.yaml `author:` + env) ----------

def configured_identity(config: dict) -> dict:
    ident = {k: str(v) for k, v in (config.get("author") or {}).items() if k in FIELDS and v not in (None, "")}
    for f, var in ENV.items():
        if os.environ.get(var):
            ident[f] = os.environ[var]
    return ident


# ---------- git range helpers ----------

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60)


def is_repo(root: Path) -> bool:
    try:
        return _git(root, "rev-parse", "--git-dir").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def cutoff_ref(config: dict) -> str | None:
    c = config.get("commits") or {}
    v = c.get("trailer_required_since")
    return str(v) if v else None


def grandfathered(root: Path, config: dict) -> set[str]:
    """Commits reachable from `commits.trailer_required_since` (inclusive): pre-cutoff history."""
    ref = cutoff_ref(config)
    if not ref:
        return set()
    res = _git(root, "rev-list", ref)
    return set(res.stdout.split()) if res.returncode == 0 else set()


def commits_in(root: Path, rng: list[str], paths: list[str] | None = None) -> list[dict]:
    """[{sha, parents, subject, trailers}] for a rev-list range (newest first)."""
    fmt = "%H%x1f%P%x1f%s%x1f%(trailers:key=OhmAI-Author,valueonly,separator=%x1d)%x1e"
    cmd = ["log", f"--format={fmt}", *rng]
    if paths:
        cmd += ["--", *paths]
    res = _git(root, *cmd)
    out = []
    for rec in res.stdout.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, parents, subject, tr = (rec.split("\x1f") + ["", "", ""])[:4]
        out.append({"sha": sha, "parents": parents.split(), "subject": subject,
                    "trailers": [t.strip() for t in tr.split("\x1d") if t.strip()]})
    return out


def phase_range(root: Path, config: dict) -> tuple[list[str], str]:
    """Commits on the current branch since its merge-base with the base branch (default main)."""
    basebr = str((config.get("commits") or {}).get("base") or "main")
    head = _git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        return [], "no commits yet"
    mb = _git(root, "merge-base", "HEAD", basebr)
    if mb.returncode != 0 and "/" not in basebr:      # CI checkouts often have only origin/main
        mb = _git(root, "merge-base", "HEAD", f"origin/{basebr}")
    if mb.returncode != 0:
        return ["HEAD"], f"all of HEAD (no merge-base with {basebr})"
    mbs = mb.stdout.strip()
    if mbs == head.stdout.strip():
        # on the base branch itself (or not diverged): the post-cutoff part of its history
        ref = cutoff_ref(config)
        return ([f"{ref}..HEAD"] if ref else []), f"on {basebr}" + (f" since {ref}" if ref else "")
    return [f"{mbs}..HEAD"], f"since merge-base {mbs[:7]} with {basebr}"


# ---------- hook ----------

PRE_PUSH_MARK = "# ohmai pre-push hook"

HOOK = f"""#!/bin/sh
{HOOK_MARK} (installed by `ohmai new` / `ohmai hooks install`; do not edit, re-run the install)
# Rejects a commit whose message has no well-formed OhmAI-Author trailer:
#   {KEY}: name=...; kind=agent|human; vendor=...; model=...; instructions=...
# kind=human needs only name and kind. `ohmai commit -m ...` appends the trailer from ohmai.yaml / OHMAI_AUTHOR_*.
msg="$1"
vals=$(grep -v '^#' "$msg" | git interpret-trailers --parse --no-divider | sed -n 's/^{KEY}[[:space:]]*:[[:space:]]*//p')
if [ -z "$vals" ]; then
  echo "commit-msg: no '{KEY}:' trailer (use 'ohmai commit -m ...' or add the trailer as the last paragraph)" >&2
  exit 1
fi
status=0
echo "$vals" | while IFS= read -r v; do
  kind=$(printf '%s' "$v" | tr ';' '\\n' | sed -n 's/^[[:space:]]*kind[[:space:]]*=[[:space:]]*//p' | sed 's/[[:space:]]*$//')
  case "$kind" in
    agent) need="name vendor model instructions" ;;
    human) need="name" ;;
    *) echo "commit-msg: {KEY}: kind must be agent or human: $v" >&2; exit 1 ;;
  esac
  for f in $need; do
    val=$(printf '%s' "$v" | tr ';' '\\n' | sed -n "s/^[[:space:]]*$f[[:space:]]*=[[:space:]]*//p" | sed 's/[[:space:]]*$//')
    if [ -z "$val" ]; then echo "commit-msg: {KEY}: $f= missing or empty: $v" >&2; exit 1; fi
  done
done || status=1
exit $status
"""

PRE_PUSH_HOOK = rf"""#!/bin/sh
{PRE_PUSH_MARK} (installed by `ohmai new` / `ohmai hooks install`; do not edit, re-run the install)
# Blocks pushing raw .pdf binary blobs when datasheets/manifest.yaml is used, *@localhost emails,
# or anonymous-mode violations when `anonymous: true` is set in ohmai.yaml.
if git ls-files '*.pdf' | grep -q '.'; then
  if [ -f datasheets/manifest.yaml ]; then
    echo "pre-push: raw .pdf files are tracked in git while datasheets/manifest.yaml is active; strip PDFs before pushing" >&2
    exit 1
  fi
fi
bad_email=$(git log -n 20 --format='%ae%n%ce' | grep -i '@localhost' | head -n 1)
if [ -n "$bad_email" ]; then
  echo "pre-push: commit author/committer email '$bad_email' uses @localhost; configure a valid or noreply email" >&2
  exit 1
fi
if [ -f ohmai.yaml ] && grep -Eq '^[[:space:]]*anonymous:[[:space:]]*true\b' ohmai.yaml; then
  bad_rev=$(git ls-files 'reviews/*/*.md' | grep -Ev '^reviews/[^/]+/(BRIEF|resolutions|owner|reviewer-[a-z0-9-]+)\.md$' | head -n 1)
  if [ -n "$bad_rev" ]; then
    echo "pre-push: anonymous mode requires role-based review filenames (owner.md or reviewer-*.md), found: $bad_rev" >&2
    exit 1
  fi
fi
# Preserve LFS's upload gate without replacing the OhmAI policy checks. Stdin
# contains git's ref update list and has not been consumed by the checks above.
if git config --get filter.lfs.process >/dev/null 2>&1; then
  if ! command -v git-lfs >/dev/null 2>&1; then
    echo "pre-push: Git LFS is configured but git-lfs is unavailable" >&2
    exit 1
  fi
  git lfs pre-push "$@" || exit $?
fi
exit 0
"""

_LOCAL_PATH_RX = re.compile(r"(?<![A-Za-z0-9_])/(?:workspace|Users|home|tmp)/(?!\.\.\.)[^\s`'\"<>)]+")
_TS_RX = re.compile(r"\b20\d{2}-\d{2}-\d{2}(?:[T\s]+~?\d{1,2}:\d{2})\b")
_ROLE_REVIEW_RX = re.compile(r"^(?:BRIEF|resolutions|owner|reviewer-[a-z0-9-]+)\.md$")


def _is_anonymous(root: Path) -> bool:
    cfg = root / "ohmai.yaml"
    if not cfg.is_file():
        return False
    try:
        import yaml
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        return bool(data.get("anonymous"))
    except Exception:
        return False


def check_pre_push(
    root: Path,
    rev_range: str = "HEAD",
    forbid_pdfs: bool | None = None,
    anonymous: bool | None = None,
) -> list[str]:
    """Programmatic pre-push / repo-hygiene guard:
    1. No tracked .pdf files when datasheets/manifest.yaml exists.
    2. No *@localhost author/committer emails.
    3. When anonymous: true in ohmai.yaml: no machine-local absolute paths (/workspace/, /Users/, /home/, /tmp/),
       no HH:MM time-of-day timestamps in docs/ or reviews/, and role-only review filenames under reviews/<phase>/.
    """
    errs: list[str] = []
    if forbid_pdfs is None:
        forbid_pdfs = (root / "datasheets" / "manifest.yaml").is_file()
    if forbid_pdfs:
        res = _git(root, "ls-files", "*.pdf", "datasheets/*.pdf")
        pdfs = sorted(set(ln.strip() for ln in res.stdout.splitlines() if ln.strip()))
        if pdfs:
            errs.append(f"raw .pdf binary blob(s) tracked in git ({len(pdfs)}): {', '.join(pdfs[:5])}")

    res = _git(root, "log", rev_range, "--format=%h %ae %ce")
    for ln in res.stdout.splitlines():
        parts = ln.split()
        if len(parts) >= 3:
            sha, ae, ce = parts[0], parts[1].lower(), parts[2].lower()
            if ae.endswith("@localhost") or ce.endswith("@localhost"):
                errs.append(f"commit {sha}: author/committer email ({ae} / {ce}) uses @localhost")

    if anonymous is None:
        anonymous = _is_anonymous(root)
    if anonymous:
        log_txt = _git(root, "log", rev_range, "--format=%h %B").stdout
        if m := _LOCAL_PATH_RX.search(log_txt):
            errs.append(f"commit log contains machine-local path '{m.group(0)}' (anonymous: true)")
        files_res = _git(root, "ls-files")
        for rel in files_res.stdout.splitlines():
            rel = rel.strip()
            if not rel:
                continue
            parts_rel = Path(rel).parts
            if len(parts_rel) == 3 and parts_rel[0] == "reviews" and rel.endswith(".md"):
                if not _ROLE_REVIEW_RX.match(parts_rel[2]):
                    errs.append(
                        f"{rel}: non-role review filename in anonymous mode "
                        "(must be BRIEF.md, resolutions.md, owner.md, or reviewer-*.md)"
                    )
            fp = root / rel
            if fp.is_file() and fp.suffix.lower() not in (".pdf", ".png", ".step", ".stp", ".glb"):
                try:
                    txt = fp.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if m := _LOCAL_PATH_RX.search(txt):
                    errs.append(f"{rel}: contains machine-local path '{m.group(0)}' (anonymous: true)")
                if rel.startswith(("docs/", "reviews/")):
                    if m2 := _TS_RX.search(txt):
                        errs.append(f"{rel}: contains HH:MM timestamp '{m2.group(0)}' (use YYYY-MM-DD in anonymous mode)")
    return errs


def hooks_dir(root: Path) -> Path:
    res = _git(root, "rev-parse", "--git-path", "hooks")
    p = Path(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else Path(".git/hooks")
    return p if p.is_absolute() else root / p


def install_hook(root: Path, force: bool = False) -> str:
    d = hooks_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    f = d / "commit-msg"
    if f.exists() and HOOK_MARK not in f.read_text(encoding="utf-8", errors="replace") and not force:
        raise FileExistsError(f"{f} exists and is not the OhmAI hook (use --force to replace it)")
    pp = d / "pre-push"
    if pp.exists() and PRE_PUSH_MARK not in pp.read_text(encoding="utf-8", errors="replace") and not force:
        raise FileExistsError(f"{pp} exists and is not the OhmAI hook (use --force to replace it)")
    f.write_text(HOOK, encoding="utf-8")
    f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    pp.write_text(PRE_PUSH_HOOK, encoding="utf-8")
    pp.chmod(pp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(f)


def hook_status(root: Path) -> tuple[bool, str]:
    d = hooks_dir(root)
    f = d / "commit-msg"
    if not f.exists():
        return False, f"{f}: missing (run `ohmai hooks install`)"
    txt = f.read_text(encoding="utf-8", errors="replace")
    if HOOK_MARK not in txt:
        return False, f"{f}: not the OhmAI hook"
    if txt != HOOK:
        return False, f"{f}: older OhmAI hook (run `ohmai hooks install`)"
    if not os.access(f, os.X_OK):
        return False, f"{f}: not executable"
    pp = d / "pre-push"
    if not pp.exists():
        return False, f"{pp}: missing (run `ohmai hooks install`)"
    pptxt = pp.read_text(encoding="utf-8", errors="replace")
    if PRE_PUSH_MARK not in pptxt or pptxt != PRE_PUSH_HOOK:
        return False, f"{pp}: missing or outdated pre-push hook (run `ohmai hooks install`)"
    if not os.access(pp, os.X_OK):
        return False, f"{pp}: not executable"
    return True, f"{f} + {pp.name}: installed"
