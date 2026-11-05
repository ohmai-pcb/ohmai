# Independent review gates

OhmAI gates that check facts line by line (constraints, schematic, layout, BOM, safety plan, manual)
need an **independent review** before the owner approves. The mechanism is plain files in the repo, so it
works with any agent (Cursor, Claude Code, others), any model vendor, or a human reviewer.

## Schematic/layout/generator review checklist
- Reuse before invent: did this create machinery OhmAI already provides?
- Could a per-sheet gate, label policy, split sheet or one placement edit suffice?
- No private maze/BFS router or generator-local `check()` hill-climb; supported tools stay capped.
- Placement commit before wires/routes; fresh exact gate output + exit before completion claims.
- Stop outranks unbounded redraw; retro targets skill/check improvements, no readability debt.
- Never cut safety, constraints, datasheet calibration or ERC/DRC/readability gates.
- End gate/stop reports with skipped/not checked + residual risk.

## Flow
1. The author finishes the phase work and runs `ohmai review start <phase>`. This writes
   `reviews/<phase>/BRIEF.md` (files in scope with a per-file and overall sha256 content hash, sources,
   locked decisions, checklist, "report findings, never edit", the required output format and the
   independence priority) and an empty `reviews/<phase>/resolutions.md`. Commit both.
2. The author asks the owner/environment for the **highest feasible independence level** and hands the
   brief to that reviewer:
   1. a model from a different vendor,
   2. a different model from the same vendor,
   3. a different agent/bot with different skills or instructions,
   4. a human.
   The author's own session or identity never counts. The level actually achieved is recorded.
3. The reviewer writes `reviews/<phase>/reviewer-a.md` (or `owner.md` for human owner sign-off; use role filenames in anonymous mode): a YAML header (`reviewer`, `kind` agent|human,
   `vendor`, `model`, `instructions`, `independence` 1-4, `independence_note`, `files_hash`, `date`) and
   a table `| ID | File | Verdict | Finding | Cite |` with at least one row per file in scope; verdict
   `OK`, `issue` or `question`, with page citations.
4. The author answers every finding in `resolutions.md`: `fixed` + commit hash, `accepted` (By = owner,
   Reason), `answered` (questions) or `pending` (By = owner: an open owner decision; WARNs on a
   question, blocks on an issue). Fixes change the files, so the author re-runs `ohmai review start`
   (the brief lists the changed files) and the reviewer re-verifies and updates `files_hash`. For a
   trivial change the owner may instead list the new hash under `accepted_changes` (WARNs).
   Reviewer-requested nits: when the reviewer's own `issue` row says `[nit]` (e.g. a citation label), the
   author may fix it after the review and resolve it as `fixed-post-review` + commit. The files named in
   those nit rows may then differ from the reviewed hash without a re-verification (WARN); any other changed
   file still FAILs. The check compares per-file hashes from the brief the reviewer used (the brief keeps
   earlier per-file hashes under `history:`, so `ohmai review start` can be re-run safely).
   Review metadata: setting `review:` (status/reviewer/date) in a constraints YAML after the review passes does
   not invalidate it: the check finds the reviewed version in git history and compares the files without the
   top-level `review:` block (WARN).
   The brief's "Changed since" list is computed against the brief the latest reviewer checked (their
   `files_hash`), not the previously generated brief.
5. `ohmai check review:<phase>` (part of `ohmai check <phase>`) must pass before the gate.

## Owner independence waiver (last resort: one review, one gate, one hash)
When a gate's `min_independence` cannot be met, the agent **first works down the priority list** (1 different
vendor's model > 2 different model, same vendor > 3 different bot/instructions > 4 human) and documents, for each
level better than the one achieved, what it tried and why that option was not available. Only then may it **ask**
the owner for a waiver; it never writes one on its own. The owner records it in that gate's
`reviews/<phase>/resolutions.md` front matter:

```yaml
accepted_independence:
  - gate: schematic                  # this gate only
    reviewer: reviewer-a                  # this review only
    level: 3                         # the level achieved
    files_hash: 'sha256:...'         # the reviewed hash; must equal the review's and the current hash
    by: owner
    owner: owner                     # role label (`owner` in anonymous mode)
    reason: '...'
    alternatives_tried: ['level 1: ... (why not available)', 'level 2: ...']
    ref: D-0xx                       # decision record (optional but recommended)
```

`ohmai check review:<phase>` then PASSes with a "WAIVED by owner ..." note listing the alternatives. A waiver
entry for this review with a missing field (`alternatives_tried` included, or `by` not owner) FAILs. A waiver
never carries over: a new `files_hash`, another reviewer or level, or another gate needs the configured level
again. Gate config and defaults are never relaxed by a waiver.

## Human sign-off gates (`require_kind`)
A gate may set `require_kind: human` next to `min_independence` (template: `bringup`, because it signs off
measurements on a real board). Then at least one review that is otherwise valid (current `files_hash`, covers
every file, not an author identity) must have `kind: human` in its header, or the gate FAILs; agent reviews
still count toward `required` and may add findings. Who may sign: a human who did not author the files in
scope. The **owner is allowed** when agents authored the work (the usual case: agents write the plan and the
manual, the owner measures and signs); an owner who committed in scope under `kind=human` trailers matches an
author identity and is rejected, so another human must sign. `kind` is self-declared in the header: the
owner vouches for it when approving the gate. `safety` stays an agent review plus owner approval.

## What the check enforces
| Condition | Result |
|-----------|--------|
| fewer valid reviews than `required` | FAIL |
| reviewer header missing a field, or does not cover every file in scope | FAIL |
| reviewer identity equals an author identity (name, session, or vendor+model+instructions) | FAIL |
| `files_hash` differs from the current files (changed after review), not owner-accepted and not only reviewer-requested `[nit]` fixes | FAIL |
| only files named in the reviewer's own `[nit]` issues changed, each resolved `fixed-post-review` + commit | WARN |
| a YAML file changed only in its top-level `review:` block since the reviewed version (git history) | WARN |
| `fixed-post-review` on an issue that is not marked `[nit]` | FAIL |
| an `issue` without a valid resolution (commit must exist; `accepted` needs owner + reason) | FAIL |
| `require_kind: human` set and no valid (current-hash, non-author) review from a `kind: human` reviewer | FAIL |
| independence weaker than `min_independence` (a larger level number; 1 = most independent), or claimed level better than the identities support | WARN |
| independence weaker than `min_independence` but listed in `resolutions.md` a complete owner waiver in `accepted_independence` (gate, reviewer, level, files_hash = reviewed and current, by: owner, owner, reason, alternatives_tried) | PASS, with a note |
| a waiver entry for this review missing a field (e.g. `alternatives_tried`) | FAIL |
| `question` without an answer; finding without a page cite; no author identity recorded | WARN |

Author identities come **first from commit trailers**
`OhmAI-Author: name=...; kind=agent|human; vendor=...; model=...; instructions=...` on the commits that
touch the files in scope. STATUS.md front matter `authors:` is a fallback only for history the trailers
cannot cover: commits reachable from `commits.trailer_required_since` (ohmai.yaml) that have no valid
trailer, or projects without git / without a cutoff (legacy behaviour).

### Trailer enforcement
| Where | What |
|---|---|
| commit-msg hook (`ohmai new`; `ohmai hooks install [--force]` in a clone; `ohmai hooks status`) | rejects a commit whose last paragraph has no well-formed trailer |
| `ohmai commit -m "..." [-a] [-- paths]` | appends the trailer from `ohmai.yaml` `author:` or `OHMAI_AUTHOR_*` env |
| `ohmai check commits` (every phase; also inside `review:<phase>`) | FAIL: a commit since the merge-base with `commits.base` (default `main`) without one valid trailer. WARN: such a commit reachable from `commits.trailer_required_since` (grandfathered), or the hook is not installed (not in CI) |

Well-formed: `kind=agent` needs non-empty name, vendor, model, instructions; `kind=human` (e.g. the
owner's merge commits) needs name. Never rewrite history to add trailers; set the cutoff instead (owner
decision, recorded in `docs/decisions.md`).

## Configuration (`ohmai.yaml`)
```yaml
reviews:
  gates:
    constraints: {required: 1, min_independence: 3, scope: ["parts/constraints/*.yaml"],
                  exclude: ["*.example.*"], sources: [datasheets/README.md],
                  extra_paths: [docs/review-constraints-x.md]}   # other accepted reviewer-file paths
```
Without a `reviews:` key the built-in defaults apply (the same as the template). `required: 0` turns a
gate's review off (owner decision; record it in `docs/decisions.md`).

## GitHub mapping
- CI (`.github/workflows/ohmai-checks.yml`) runs `ohmai check current`, which includes `review:<phase>`,
  so a PR cannot go green without the review files. Checkout uses `fetch-depth: 0` so commit refs in
  `resolutions.md` and `OhmAI-Author` trailers are visible.
- The reviewer may open the review file as its own PR or commit it to the phase branch; either way the
  file in the repo is the record (it survives a vendor or tool switch).
- Optionally also require a GitHub PR review: branch protection on `main` → "Require a pull request
  before merging" + "Require approvals" (and "Require review from Code Owners" with a `CODEOWNERS` entry
  for `reviews/`). A PR approval is the owner's gate; it does not replace the independent review file.
