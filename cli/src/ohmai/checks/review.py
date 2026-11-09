# SPDX-License-Identifier: Apache-2.0
"""`review` check: the independent review gate for a phase (see ohmai/review.py)."""

from __future__ import annotations

import re

from ohmai.checks import FAIL, PASS, WARN, Result
from ohmai.project import Project


def _post_review_ok(project, phase, r, rp, who, cur, nit_files, res_rows, errs, warns) -> bool:
    """A review stays valid after a change if every changed file is covered by one of the reviewer's own
    [nit] issues resolved as `fixed-post-review` with an existing commit (reviewer-requested trivial fix), or if
    it differs from the reviewed version (found in git history) only in its `review:` metadata block."""
    from ohmai import review as rv

    old_hash = str(r.header.get("files_hash"))
    base = rv.baselines(project, phase).get(old_hash)
    _, now = rv.content_hash(project, rv.scope_files(project, rv.gates(project)[phase]))
    stale_msg = (f"{rp}: files changed after review (hash {old_hash[:19]}... != {cur[:19]}...): run "
                 f"`ohmai review start {phase}` and have the reviewer re-verify")
    if base is None:
        errs.append(stale_msg + " (no brief with the reviewed per-file hashes)")
        return False
    changed = {k for k in set(base) | set(now) if base.get(k) != now.get(k)}
    allowed: set[str] = set()
    for row in res_rows:
        if row.get("resolution", "").strip().lower() != rv.POST_REVIEW:
            continue
        if row.get("reviewer") and row["reviewer"].strip().lower() != who.lower():
            continue
        fid = row.get("id", "").strip()
        ref = (row.get("ref") or "").strip("` ")
        if fid in nit_files and rv._HEX.match(ref) and rv.commit_exists(project, ref) is not False:
            allowed |= nit_files[fid]
    meta = {k for k in changed - allowed if k in base and rv.metadata_only_change(project, k, base[k])}
    rest = sorted(changed - allowed - meta)
    if rest:
        errs.append(stale_msg + f"; changed and not covered by a reviewer-requested nit fix: {', '.join(rest[:5])}")
        return False
    if allowed & changed:
        warns.append(f"{rp}: {len(allowed & changed)} file(s) changed after review by reviewer-requested nit fixes "
                     f"({rv.POST_REVIEW}): {', '.join(sorted(allowed & changed))}")
    if meta:
        warns.append(f"{rp}: {len(meta)} file(s) changed after review only in review metadata "
                     f"({', '.join(rv.META_KEYS)}:)")
    return True


# Work checks that are a hard part of a phase's review gate: a FAIL here can never be accepted as debt, waived,
# or carried past the gate (a dogfood board's Phase 7 reached "review PASS" with 167 readability problems accepted as
# debt, while the previous project, reference-board, had to be drawn clean). Fix the drawing, then re-review.
HARD_GATE_CHECKS: dict[str, tuple[str, ...]] = {"schematic": ("readability",)}


def check_review(project: Project, phase_name: str | None) -> Result:
    from ohmai import review as rv
    from ohmai.status import load

    name = f"review:{phase_name}" if phase_name else "review"
    if not phase_name:
        ph = load(project.root / "STATUS.md").phase
        if ph is None:
            return Result(name, FAIL, "STATUS.md has no valid phase")
        phase = ph.id
    else:
        phase = rv.phase_id(phase_name)
    gate = rv.gates(project).get(phase)
    if gate is None or gate.required < 1:
        return Result(name, PASS, f"no independent review required for '{phase}'")
    files = rv.scope_files(project, gate)
    cur, _ = rv.content_hash(project, files)
    auths = rv.authors(project, files)
    reviews = rv.load_reviews(project, gate)
    res_hdr, res_rows = rv.load_resolutions(project, phase)
    errs, warns, notes = [], [], []   # notes: owner-accepted waivers, reported without changing the status
    from ohmai.checks.commits import check_commits

    cr = check_commits(project, {})
    if cr.status == FAIL:
        errs.append(f"commits: {cr.summary} (authors cannot be proven; `ohmai check commits`)")
        errs += [f"commits: {d}" for d in cr.details[:10]]
    elif cr.status == WARN:
        warns.append(f"commits: {cr.summary}")
    for hc in HARD_GATE_CHECKS.get(phase, ()):
        from ohmai.checks import run as run_check

        hr = run_check(project, hc, {})
        if hr.status == FAIL:
            errs.append(f"{hc}: {hr.summary}: hard gate, not waivable or acceptable as debt; fix the sheets "
                        f"(`ohmai check {hc}` must PASS before the {phase} gate)")
    if not auths:
        warns.append("no author identity recorded (STATUS.md `authors:` or `OhmAI-Author:` commit trailers); "
                     "reviewer independence cannot be checked")
    if not (rv.review_dir(project, phase) / "BRIEF.md").is_file():
        warns.append(f"no reviews/{phase}/BRIEF.md (run `ohmai review start {phase}`)")
    accepted_hashes = {str(a.get("files_hash")) for a in (res_hdr.get("accepted_changes") or [])
                       if isinstance(a, dict) and str(a.get("by", "")).lower() == "owner" and a.get("reason")}
    rel = {f.relative_to(project.root).as_posix() for f in files}
    valid = 0
    valid_kind = 0
    findings: list[tuple[str, str, str, dict]] = []   # (reviewer, id, verdict, row)
    for r in reviews:
        rp = r.path.relative_to(project.root).as_posix()
        who = str(r.header.get("reviewer") or r.path.stem)
        if r.errors:
            errs += [f"{rp}: {e}" for e in r.errors]
            continue
        ok = True
        for a in auths:
            if rv.same_identity(r.header, a):
                errs.append(f"{rp}: reviewer identity matches author '{a.get('name')}' (not independent)")
                ok = False
                break
        try:
            lvl = int(r.header.get("independence"))
        except (TypeError, ValueError):
            errs.append(f"{rp}: independence must be 1-4")
            continue
        if lvl not in rv.LEVELS:
            errs.append(f"{rp}: independence must be 1-4")
            continue
        der = rv.derived_level(r.header, auths)
        if der is not None and der > lvl:
            warns.append(f"{rp}: claims independence {lvl} but the identities support only {der}; using {der}")
            lvl = der
        waiver = None
        if lvl > gate.min_independence:
            waiver = _independence_waiver(res_hdr, phase, who, lvl, str(r.header.get("files_hash")), cur, rp, errs)
        if waiver:
            notes.append(f"{rp}: independence level {lvl} below this gate's level {gate.min_independence}: "
                         f"WAIVED by owner {waiver.get('owner')} ({waiver.get('reason')}"
                         + (f", {waiver['ref']}" if waiver.get("ref") else "") + "); alternatives tried: "
                         + "; ".join(_as_list(waiver.get("alternatives_tried"))))
        elif lvl > gate.min_independence:
            warns.append(f"{rp}: independence level {lvl} ({rv.LEVELS[lvl]}) is less independent than this gate "
                         f"requires: level {gate.min_independence} or better (1 = most independent, 4 = least)")
        stale = str(r.header.get("files_hash")) != cur
        if stale and cur in accepted_hashes:
            warns.append(f"{rp}: files changed after review; accepted by the owner (resolutions.md)")
            stale = False
        covered = set()
        nit_files: dict[str, set[str]] = {}   # finding id -> files of this reviewer's own [nit] issues
        for i, row in enumerate(r.rows, start=1):
            f = row.get("file", "").strip("` ")
            verdict = row.get("verdict", "").strip().lower()
            match = {x for x in rel if x == f or x.endswith("/" + f)}
            covered |= match
            if verdict not in ("ok", "issue", "question"):
                errs.append(f"{rp}: row {i}: verdict must be OK, issue or question, not {row.get('verdict')!r}")
                continue
            if verdict != "ok":
                if not row.get("cite"):
                    warns.append(f"{rp}: {row.get('id') or i}: {verdict} without a page citation")
                findings.append((who, row.get("id") or f"row{i}", verdict, row))
                if verdict == "issue" and re.search(r"\bnit\b", row.get("finding", ""), re.I):
                    nit_files[row.get("id") or f"row{i}"] = match
        if stale:
            ok = _post_review_ok(project, phase, r, rp, who, cur, nit_files, res_rows, errs, warns) and ok
        missing = sorted(rel - covered)
        if missing:
            errs.append(f"{rp}: does not cover {len(missing)} file(s): {', '.join(missing[:5])}"
                        + (" ..." if len(missing) > 5 else ""))
            ok = False
        if ok:
            valid += 1
            if gate.require_kind and str(r.header.get("kind") or "").strip().lower() == gate.require_kind:
                valid_kind += 1
    if valid < gate.required:
        errs.insert(0, f"{valid} valid independent review(s), {gate.required} required "
                       f"(brief: reviews/{phase}/BRIEF.md; write reviews/{phase}/<reviewer>.md"
                       + (f" or {', '.join(gate.extra_paths)}" if gate.extra_paths else "") + ")")
    if gate.require_kind and gate.require_kind not in ("agent", "human"):
        errs.append(f"ohmai.yaml reviews.gates.{phase}.require_kind must be agent or human, not {gate.require_kind!r}")
    elif gate.require_kind and valid_kind < 1:
        errs.insert(0, f"no valid review from a kind={gate.require_kind} reviewer (gate require_kind: "
                       f"{gate.require_kind}; a {gate.require_kind} who did not author the files in scope, the owner "
                       "allowed when agents authored them; header `kind: human`, current files_hash)")
    # resolutions
    by_id: dict[str, list[dict]] = {}
    for row in res_rows:
        rid = row.get("id", "").strip()
        by_id.setdefault(rid, []).append(row)
    open_issues = 0
    for who, fid, verdict, _row in findings:
        cands = [r for r in by_id.get(fid, []) if not r.get("reviewer") or r["reviewer"].strip().lower() == who.lower()]
        res = cands[-1] if cands else None
        kind = (res or {}).get("resolution", "").strip().lower()
        label = f"{who}/{fid}"
        if verdict == "question" and kind in ("answered", "fixed", "accepted"):
            continue
        if verdict == "question":
            warns.append(f"{label}: question pending owner decision" if kind == "pending"
                         else f"{label}: question has no answer in resolutions.md")
            continue
        if kind == "pending":
            errs.append(f"{label}: issue pending owner decision (blocks the gate until fixed or accepted)")
            open_issues += 1
            continue
        if kind == rv.POST_REVIEW and not re.search(r"\bnit\b", _row.get("finding", ""), re.I):
            errs.append(f"{label}: '{rv.POST_REVIEW}' is only for the reviewer's own [nit] issues; use 'fixed' "
                        "and get a re-verification")
            open_issues += 1
            continue
        if kind in ("fixed", rv.POST_REVIEW):
            ref = (res.get("ref") or "").strip("` ")
            if not rv._HEX.match(ref):
                errs.append(f"{label}: 'fixed' needs a commit hash in Ref")
                open_issues += 1
            elif rv.commit_exists(project, ref) is False:
                errs.append(f"{label}: commit {ref} not found")
                open_issues += 1
        elif kind == "accepted":
            if (res.get("by") or "").strip().lower() != "owner" or not (res.get("reason") or "").strip():
                errs.append(f"{label}: 'accepted' needs By = owner and a Reason")
                open_issues += 1
        else:
            errs.append(f"{label}: issue has no resolution (fixed + commit, or accepted by owner with reason)")
            open_issues += 1
    summary = (f"{phase}: {valid}/{gate.required} review(s), {len(findings)} finding(s), "
               f"{open_issues} unresolved issue(s)")
    if errs:
        return Result(name, FAIL, summary, errs + warns + notes)
    return Result(name, WARN if warns else PASS, summary, warns + notes)


WAIVER_KEYS = ("gate", "reviewer", "level", "files_hash", "by", "owner", "reason", "alternatives_tried")


def _as_list(v) -> list[str]:
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v).strip()] if v is not None and str(v).strip() else []


def _independence_waiver(res_hdr: dict, phase: str, who: str, lvl: int, rev_hash: str, cur: str, rp: str,
                         errs: list[str]) -> dict | None:
    """Owner waiver for ONE review below min_independence: this gate, this reviewer, this level and this
    files_hash (= the review's and the current hash). Every WAIVER_KEYS field is required; an entry that
    targets this review but misses a field (e.g. alternatives_tried) FAILs."""
    for w in res_hdr.get("accepted_independence") or []:
        if not isinstance(w, dict) or str(w.get("reviewer", "")).strip().lower() != who.strip().lower():
            continue
        if str(w.get("files_hash", "")).strip() != rev_hash or rev_hash != cur:
            continue                                  # a waiver never carries over to another hash
        if str(w.get("gate", phase)).strip().lower() != phase or str(w.get("level", "")).strip() != str(lvl):
            continue
        missing = [k for k in WAIVER_KEYS if k != "alternatives_tried" and not str(w.get(k) or "").strip()]
        if not _as_list(w.get("alternatives_tried")):
            missing.append("alternatives_tried")
        if str(w.get("by", "")).strip().lower() != "owner":
            missing.append("by: owner")
        if missing:
            errs.append(f"{rp}: independence waiver in reviews/{phase}/resolutions.md is incomplete: "
                        f"{', '.join(missing)} (the agent must first try and document every higher level)")
            return None
        return w
    return None
