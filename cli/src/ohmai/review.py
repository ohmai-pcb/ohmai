"""Independent review gates: config, content hash, BRIEF.md, reviewer files, resolutions.

Tool-agnostic: everything is plain files in the repo (reviews/<phase>/). Any agent, any vendor or a human
can read BRIEF.md and write a reviewer file; `ohmai check review` verifies it.
"""

from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ohmai import __version__
from ohmai.phases import resolve
from ohmai.project import Project

# Independence levels, best first. The author asks for the highest feasible one and records the one achieved.
LEVELS = {
    1: "different vendor's model (e.g. author on vendor A, reviewer on vendor B)",
    2: "different model from the same vendor",
    3: "different agent/bot with different skills or instructions (same model allowed)",
    4: "human reviewer",
}
# Same agent session or identity as the author is never a review.

DEFAULT_GATES: dict[str, dict] = {
    "safety": {"required": 1, "min_independence": 4, "scope": ["docs/safety.md"], "sources": []},
    "constraints": {"required": 1, "min_independence": 3, "scope": ["parts/constraints/*.yaml"],
                    "exclude": ["*.example.*"], "sources": ["datasheets/README.md"]},
    "schematic": {"required": 1, "min_independence": 2, "scope": ["hardware/*.kicad_sch", "expected_nets.csv"],
                  "sources": ["parts/constraints/*.yaml"]},
    "layout": {"required": 1, "min_independence": 2, "scope": ["hardware/*.kicad_pcb"],
               "sources": ["parts/constraints/*.yaml"]},
    "bom": {"required": 1, "min_independence": 3, "scope": ["bom/bom.csv"], "sources": ["parts/selection.md"]},
    "fab": {"required": 1, "min_independence": 4, "require_kind": "human", "scope": ["bom/bom.csv", "hardware/*.kicad_pcb"],
            "sources": ["parts/selection.md", "docs/safety.md"]},
    "bringup": {"required": 1, "min_independence": 4, "require_kind": "human", "scope": ["docs/manual.md", "test/bringup-plan.md"],
                "sources": ["docs/safety.md"]},
}

CHECKLISTS: dict[str, list[str]] = {
    "constraints": [
        "Every pin of the part is listed; number and name match the datasheet pin table (cite the page).",
        "Every direction is right for the pin function; no `unknown` left without an open question.",
        "Abs max / recommended values, units and min/max/nominal match the cited page exactly.",
        "Required external parts (value, tolerance, `to:` pin or net, total_on_net) match the datasheet or reference design.",
        "Every cite points at the page number printed on the page, and the page really says it.",
        "Open questions are real questions; resolutions marked in the file match docs/decisions.md.",
    ],
    "schematic": [
        "Every pin connection matches parts/constraints/<PART>.yaml and expected_nets.csv.",
        "Values, ratings and derating (docs/safety.md) of every passive; pull-ups, strapping, I2C addresses.",
        "Power tree, decoupling per pin, reset/boot, ESD and connector pinout (cite pages).",
    ],
    "layout": [
        "Placement and loops per the datasheet layout notes (cite pages); creepage/clearance per docs/safety.md.",
        "Footprints, pin 1 and rotation for every IC and polarised part; fab rules (drill, annular ring, mask).",
        "Owner visual/mechanical sign-off on 2D/3D renders (docs/layout/routed-2d.png, docs/layout/routed-3d.png: connector positions, button/LED ergonomics, keepouts, mounting).",
    ],
    "bom": [
        "Every row: MPN, footprint, rating, temperature, lifecycle, stock, price source and date.",
        "Second source or written single-source acceptance per row; refs match the schematic.",
    ],
    "fab": [
        "Gerbers and drill files inspected in a viewer (board outline, copper layers, solder mask, silkscreen, tented/untented vias).",
        "CPL / PnP centroids, rotations, and pin-1/polarity marks verified against the fab DFM preview for every IC, diode, LED, switch, and connector.",
        "Final locked bom/bom.csv and SINGLE-SOURCE COMPONENTS highlight block reviewed and approved before ordering.",
    ],
    "safety": [
        "Hazards, target-market legislation and standards cited; derating rules complete.",
        "Every required manual warning (MW-xx) is justified and listed.",
    ],
    "bringup": [
        "Every test has a pass/fail limit with a source; the manual covers every MW-xx and market rule.",
    ],
}

REVIEWER_KEYS = ("reviewer", "kind", "vendor", "model", "instructions", "independence", "files_hash", "date")
# owner.md is the owner sign-off for the gate (skill section 3), not an independent review.
SPECIAL = {"BRIEF.md", "resolutions.md", "README.md", "owner.md"}
_HEX = re.compile(r"^[0-9a-f]{7,40}$")


@dataclass
class Gate:
    phase: str
    required: int
    min_independence: int
    scope: list[str]
    exclude: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    extra_paths: list[str] = field(default_factory=list)
    note: str = ""
    require_kind: str = ""   # "human": at least one valid review must come from a kind=human reviewer


def gates(project: Project) -> dict[str, Gate]:
    cfg = project.config.get("reviews")
    raw = DEFAULT_GATES if not isinstance(cfg, dict) else (cfg.get("gates") or {})
    out = {}
    for ph, g in raw.items():
        if not isinstance(g, dict):
            continue
        base = {**DEFAULT_GATES.get(ph, {}), **g}
        out[ph] = Gate(ph, int(base.get("required", 1)), int(base.get("min_independence", 4)),
                       list(base.get("scope") or []), list(base.get("exclude") or []),
                       list(base.get("sources") or []), list(base.get("extra_paths") or []),
                       str(base.get("note") or ""), str(base.get("require_kind") or "").strip().lower())
    return out


def phase_id(name: str) -> str:
    ph = resolve(name)
    if ph is None:
        raise SystemExit(f"ohmai review: unknown phase '{name}'")
    return ph.id


def review_dir(project: Project, phase: str) -> Path:
    return project.root / "reviews" / phase


def _glob(project: Project, patterns: list[str], exclude: list[str]) -> list[Path]:
    files: set[Path] = set()
    for pat in patterns:
        for f in project.root.glob(pat):
            if f.is_file() and not any(fnmatch.fnmatch(f.name, x) for x in exclude):
                files.add(f)
    return sorted(files)


def scope_files(project: Project, gate: Gate) -> list[Path]:
    return _glob(project, gate.scope, gate.exclude)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def content_hash(project: Project, files: list[Path]) -> tuple[str, dict[str, str]]:
    per = {f.relative_to(project.root).as_posix(): file_hash(f) for f in files}
    h = hashlib.sha256("".join(f"{v}  {k}\n" for k, v in sorted(per.items())).encode()).hexdigest()
    return f"sha256:{h}", per


# ---------- identities ----------

def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def parse_identity(value: str) -> dict:
    """'name=OhmAI; kind=agent; vendor=X; model=Y; instructions=Z' (or just a name)."""
    if "=" not in value:
        return {"name": value.strip()}
    out = {}
    for part in value.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def authors(project: Project, files: list[Path] | None = None) -> list[dict]:
    """Author identities of the files in scope.

    Trailers first: every `OhmAI-Author:` trailer on a commit touching the files. STATUS.md front matter
    `authors:` is only a fallback for history the trailers cannot cover: commits before
    `commits.trailer_required_since` (ohmai.yaml) without a valid trailer, or no git / no cutoff configured
    (legacy projects). A post-cutoff commit without a trailer is a `commits` check FAIL, not a fallback."""
    from ohmai import trailers as tr
    from ohmai.status import load

    out: list[dict] = []
    fallback = True
    if tr.is_repo(project.root):
        paths = [str(f.relative_to(project.root)) for f in files] if files else None
        try:
            commits = tr.commits_in(project.root, ["HEAD"], paths)
        except (OSError, subprocess.SubprocessError):
            commits = []
        old = tr.grandfathered(project.root, project.config)
        cutoff = tr.cutoff_ref(project.config)
        fallback = not cutoff or not commits
        for c in commits:
            valid = [v for v in c["trailers"] if not tr.validate(v)]
            if c["sha"] in old and not valid:
                fallback = True
            for v in c["trailers"]:
                ident = parse_identity(v)
                if ident not in out and not _covered(ident, out):
                    out.append(ident)
    if fallback:
        st = load(project.root / "STATUS.md")
        status = [a if isinstance(a, dict) else {"name": str(a)} for a in (st.meta.get("authors") or [])]
        out = status + [a for a in out if a not in status and not _covered(a, status)]
    return out


def _covered(ident: dict, known: list[dict]) -> bool:
    """A name-only identity (free-form trailer such as 'OhmAI (vendor, skill)') adds nothing when a fuller
    identity with the same name is already known."""
    if set(ident) != {"name"}:
        return False
    n = _norm(ident["name"])
    base = _norm(str(ident["name"]).split("(")[0])
    return any(_norm(k.get("name")) in (n, base) for k in known if len(k) > 1)


def same_identity(rev: dict, auth: dict) -> bool:
    if rev.get("session") and _norm(rev.get("session")) == _norm(auth.get("session")):
        return True
    if _norm(rev.get("reviewer") or rev.get("name")) and \
            _norm(rev.get("reviewer") or rev.get("name")) == _norm(auth.get("name")):
        return True
    keys = ("vendor", "model", "instructions")
    return all(_norm(rev.get(k)) and _norm(rev.get(k)) == _norm(auth.get(k)) for k in keys)


def derived_level(rev: dict, auths: list[dict]) -> int | None:
    """Best level the identities support (None when an identity field is unknown)."""
    if _norm(rev.get("kind")) == "human":
        return 4
    worst = 1
    for a in auths:
        rv, av = _norm(rev.get("vendor")), _norm(a.get("vendor"))
        rm, am = _norm(rev.get("model")), _norm(a.get("model"))
        if not rv or not av or "unknown" in (rv, av):
            return None
        if rv != av:
            lvl = 1
        elif rm and am and rm != am:
            lvl = 2
        else:
            lvl = 3
        worst = max(worst, lvl)
    return worst if auths else None


# ---------- reviewer files ----------

def split_front(text: str) -> tuple[dict | None, str, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None, text, "no YAML header (--- ... ---)"
    try:
        return (yaml.safe_load(m.group(1)) or {}), text[m.end():], ""
    except yaml.YAMLError as e:
        return None, text, f"YAML header invalid: {e}"


def tables(body: str) -> list[dict]:
    rows, out, head = [], [], None
    for ln in body.splitlines() + [""]:
        s = ln.strip()
        if s.startswith("|"):
            rows.append([c.strip() for c in s.strip("|").split("|")])
            continue
        if len(rows) >= 2:
            head = [h.lower() for h in rows[0]]
            for r in rows[2:]:
                if any(r):
                    out.append(dict(zip(head, r, strict=False)))
        rows = []
    return out


@dataclass
class Review:
    path: Path
    header: dict
    rows: list[dict]
    errors: list[str]


def load_reviews(project: Project, gate: Gate) -> list[Review]:
    d = review_dir(project, gate.phase)
    paths = sorted(p for p in d.glob("*.md") if p.name.lower() not in {n.lower() for n in SPECIAL}) if d.is_dir() else []
    paths += [project.root / p for p in gate.extra_paths if (project.root / p).is_file()]
    out, seen = [], set()
    for p in paths:
        digest = file_hash(p)
        if digest in seen:          # the same review saved at two accepted paths counts once
            continue
        seen.add(digest)
        hdr, body, err = split_front(p.read_text(encoding="utf-8"))
        errs = [err] if err else []
        hdr = hdr or {}
        if not err:
            errs += [f"header missing '{k}'" for k in REVIEWER_KEYS if hdr.get(k) in (None, "")]
            if hdr.get("kind") not in (None, "agent", "human"):
                errs.append("kind must be agent or human")
        # only the findings table (ID/File/Verdict); other tables in the narrative are ignored
        rows = [r for r in tables(body) if "verdict" in r and "file" in r]
        out.append(Review(p, hdr, rows, errs))
    return out


def load_resolutions(project: Project, phase: str) -> tuple[dict, list[dict]]:
    p = review_dir(project, phase) / "resolutions.md"
    if not p.is_file():
        return {}, []
    hdr, body, _ = split_front(p.read_text(encoding="utf-8"))
    return hdr or {}, [r for r in tables(body) if "resolution" in r]


def baselines(project: Project, phase: str) -> dict[str, dict]:
    """files_hash -> {file: sha256} from BRIEF.md (current + history)."""
    p = review_dir(project, phase) / "BRIEF.md"
    if not p.is_file():
        return {}
    hdr, _, _ = split_front(p.read_text(encoding="utf-8"))
    hdr = hdr or {}
    out = {str(k): v for k, v in (hdr.get("history") or {}).items() if isinstance(v, dict)}
    if isinstance(hdr.get("files"), dict):
        out[str(hdr.get("files_hash"))] = hdr["files"]
    return out


POST_REVIEW = "fixed-post-review"
META_KEYS = ("review",)   # top-level YAML keys that record the review itself, not reviewed content


def _blob_with_hash(project: Project, rel: str, digest: str) -> bytes | None:
    """The content of `rel` in git history whose (CRLF-normalized) sha256 is `digest`."""
    if not (project.root / ".git").exists():
        return None
    try:
        revs = subprocess.run(["git", "-C", str(project.root), "log", "--format=%H", "--", rel],
                              capture_output=True, text=True, timeout=30).stdout.split()
        for c in revs[:50]:
            b = subprocess.run(["git", "-C", str(project.root), "show", f"{c}:{rel}"],
                               capture_output=True, timeout=30).stdout
            if hashlib.sha256(b.replace(b"\r\n", b"\n")).hexdigest() == digest:
                return b
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def metadata_only_change(project: Project, rel: str, base_digest: str) -> bool:
    """True when a YAML file differs from its reviewed version only in META_KEYS (e.g. review.status set to
    'reviewed' after the review passed)."""
    if not rel.endswith((".yaml", ".yml")):
        return False
    old = _blob_with_hash(project, rel, base_digest)
    if old is None:
        return False
    try:
        a = yaml.safe_load(old.decode("utf-8"))
        b = yaml.safe_load((project.root / rel).read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError, OSError):
        return False
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    return {k: v for k, v in a.items() if k not in META_KEYS} == {k: v for k, v in b.items() if k not in META_KEYS}


def commit_exists(project: Project, ref: str) -> bool | None:
    if not (project.root / ".git").exists():
        return None
    try:
        r = subprocess.run(["git", "-C", str(project.root), "cat-file", "-e", f"{ref}^{{commit}}"],
                           capture_output=True, timeout=15)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return None


# ---------- brief ----------

def _identity_line(a: dict) -> str:
    return "; ".join(f"{k}={a[k]}" for k in ("name", "kind", "vendor", "model", "instructions", "session") if a.get(k))


def _sources(project: Project, gate: Gate, files: list[Path]) -> list[str]:
    src: list[str] = []
    if gate.phase == "constraints":
        for f in files:
            try:
                ds = ((yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("datasheet") or {}).get("file")
            except yaml.YAMLError:
                ds = None
            if ds and ds not in src:
                src.append(ds)
    for f in _glob(project, gate.sources, []):
        r = f.relative_to(project.root).as_posix()
        if r not in src:
            src.append(r)
    return src


def start(project: Project, phase_name: str, today: str | None = None) -> Path:
    from ohmai.status import load

    phase = phase_id(phase_name)
    gs = gates(project)
    if phase not in gs or gs[phase].required < 1:
        raise SystemExit(f"ohmai review start: no review gate configured for '{phase}' (ohmai.yaml reviews:)")
    gate = gs[phase]
    files = scope_files(project, gate)
    if not files:
        raise SystemExit(f"ohmai review start: no files match the scope {gate.scope}")
    audit_details: list[str] = []
    if phase == "layout":
        from ohmai.checks.placement import placement_check
        pres = placement_check(project, {})
        if pres.status == "FAIL":
            msg = "; ".join(pres.details[:5]) or pres.summary
            raise SystemExit(f"ohmai review start layout: pre-review self-audit failed ({pres.summary}): {msg}")
        if pres.status == "PASS":
            audit_details = list(pres.details)
    h, per = content_hash(project, files)
    auths = authors(project, files)
    st = load(project.root / "STATUS.md")
    today = today or _dt.date.today().isoformat()
    d = review_dir(project, phase)
    d.mkdir(parents=True, exist_ok=True)
    prev_hdr, _, _ = split_front((d / "BRIEF.md").read_text(encoding="utf-8")) if (d / "BRIEF.md").is_file() else (None, "", "")
    # "changed since": against the brief the latest reviewer actually checked (files_hash in their header),
    # not against whatever brief was generated last; fall back to the previous brief
    changed, since = [], None
    base = baselines(project, phase)
    reviewed = [r for r in load_reviews(project, gate) if str(r.header.get("files_hash")) in base]
    reviewed.sort(key=lambda r: str(r.header.get("date") or ""))
    old = None
    if reviewed:
        r = reviewed[-1]
        old = base[str(r.header["files_hash"])]
        since = f"the brief last reviewed by {r.header.get('reviewer') or r.path.stem} ({str(r.header['files_hash'])[:19]}...)"
    elif prev_hdr and isinstance(prev_hdr.get("files"), dict):
        old, since = prev_hdr["files"], "the previous brief"
    if old is not None:
        changed = sorted(k for k in set(old) | set(per) if old.get(k) != per.get(k))

    # keep the per-file hashes of earlier briefs: a review made against an earlier brief can then be
    # matched file by file (post-review nit fixes, see checks/review.py)
    history = dict((prev_hdr or {}).get("history") or {})
    if prev_hdr and isinstance(prev_hdr.get("files"), dict) and prev_hdr.get("files_hash") != h:
        history[str(prev_hdr.get("files_hash"))] = prev_hdr["files"]
    history = dict(list(history.items())[-10:])
    head = {"ohmai_review_brief": 1, "ohmai_version": __version__, "phase": phase, "created": today,
            "files_hash": h, "required_reviews": gate.required, "min_independence": gate.min_independence, **({"require_kind": gate.require_kind} if gate.require_kind else {}),
            "authors": auths, "files": per}
    if history:
        head["history"] = history
    L = ["---", yaml.safe_dump(head, sort_keys=False, allow_unicode=True).rstrip(), "---",
         f"# Independent review brief: phase `{phase}` ({st.meta.get('project', project.root.name)})", "",
         "This brief is self-contained. You need only this repository and the files listed here.", "",
         "## Your role",
         "- You are an **independent reviewer**. **Report findings, never edit** the files in scope (or any other",
         "  project file). Write only your own review file (format below).",
         "- Check every line of every file in scope against the cited source page. Do not trust the author's notes.",
         "- Cite the page number printed on the source page (plus section/table), for every issue and question.",
         "- Do not invent values. If a source does not say it, write a `question`.", "",
         "## Reviewer independence (state which level you are)",
         "Ask for and use the highest feasible level; record the level actually achieved in your header:"]
    L += [f"{n}. {t}" for n, t in LEVELS.items()]
    L += ["Not allowed: the same agent session or identity as the author (see Authors). That is not a review.",
          f"Required for this gate: level {gate.min_independence} or better (a smaller number; 1 = most independent). "
          "A weaker level (larger number) is recorded and WARNs.",
          *([f"This gate requires a **kind: {gate.require_kind}** reviewer: at least one valid review must come from "
             f"a {gate.require_kind} who did not author the files in scope (the owner may sign off when agents "
             "authored the work). Without it the gate FAILs.", ""] if gate.require_kind else [""]),
          "## Authors (the reviewer must differ)"]
    L += [f"- {_identity_line(a)}" for a in auths] or ["- (none recorded: STATUS.md `authors:` / `OhmAI-Author:` commit trailers)"]
    L += ["", f"## Files in scope ({len(per)}), content hash `{h}`", "", "| File | sha256 |", "|---|---|"]
    L += [f"| `{k}` | `{v[:12]}` |" for k, v in sorted(per.items())]
    if changed:
        L += ["", f"### Changed since {since} (verify these first)"] + [f"- `{c}`" for c in changed]
    L += ["", "## Sources"]
    L += [f"- `{s}`" for s in _sources(project, gate, files)] or ["- (see the cite fields in each file)"]
    L += ["", "## Locked decisions (do not re-open; flag a conflict as `question`)"]
    L += [f"- {r.get('id', '')}: {r.get('decision', '')}" for r in st.decisions] or ["- (none)"]
    L += ["", "## Checklist"] + [f"- [ ] {c}" for c in CHECKLISTS.get(phase, ["Every line against its source."])]
    if audit_details:
        L += ["", "### Pre-review self-audit (`ohmai check placement`)"] + [f"- {d}" for d in audit_details]
    L += ["", "## Required output",
          f"Write `reviews/{phase}/<your-name>.md`"
          + (" (also accepted: " + ", ".join(f"`{p}`" for p in gate.extra_paths) + ")" if gate.extra_paths else "")
          + " exactly in this format:", "", "```markdown", "---",
          "reviewer: <your name>            # must differ from every author",
          "kind: agent                      # agent | human",
          "vendor: <model vendor, or 'n/a' for a human>",
          "model: <model name and version, or 'n/a'>",
          "instructions: <skill / system-prompt identity, e.g. 'reviewer-a skill v3'>",
          "independence: <1-4, the level achieved (see above)>",
          "independence_note: <what you asked for and why this level>",
          f"files_hash: {h}",
          f"date: {today}", "---",
          "| ID | File | Verdict | Finding | Cite |", "|----|------|---------|---------|------|",
          f"| F1 | {next(iter(sorted(per)))} | OK | | |",
          "| F2 | <file> | issue | <what is wrong and the correct value> | p.<n> <section> |",
          "| F3 | <file> | question | <what the source leaves open> | p.<n> |", "```", "",
          "- One row at least per file in scope; `Verdict` is `OK`, `issue` or `question`.",
          "- `files_hash` must be the hash above; if the files change, the author regenerates this brief and you",
          "  re-verify and update your hash.",
          f"- The author answers every issue in `reviews/{phase}/resolutions.md` (`fixed` + commit, or `accepted`",
          "  by the owner with a reason). You do not write resolutions."]
    if gate.note:
        L += ["", "## Note", gate.note]
    p = d / "BRIEF.md"
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    if not (d / "resolutions.md").is_file():
        (d / "resolutions.md").write_text(
            "---\n# Owner may accept a file change after review without re-review (WARNs):\n"
            "# accepted_changes: [{files_hash: 'sha256:...', by: owner, reason: '...'}]\naccepted_changes: []\n"
            "# Owner waiver for one review below min_independence, this gate and this files_hash only (PASS + note):\n"
            "# accepted_independence: [{gate: <phase>, reviewer: <name>, level: 3, files_hash: 'sha256:<reviewed hash>',\n"
            "#   by: owner, owner: <name>, reason: '...', alternatives_tried: ['level 1: ...', 'level 2: ...'], ref: D-0xx}]\n---\n"
            f"# Resolutions: phase `{phase}` review findings\n\n"
            "Resolution: `fixed` (Ref = commit hash), `accepted` (By = owner, Reason required), `answered` (questions)\n"
            "`pending` (By = owner; an open owner decision), or `fixed-post-review` (Ref = commit; only for the\n"
            "reviewer's own [nit] issues: the files they name may change after the review without re-verification).\n\n"
            "| ID | Reviewer | Resolution | Ref | By | Reason |\n|----|----------|------------|-----|----|--------|\n",
            encoding="utf-8")
    return p
