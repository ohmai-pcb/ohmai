<!-- SPDX-License-Identifier: Apache-2.0 -->
# AGENTS.md: contributing to the OhmAI tool repo

For agents and humans changing OhmAI itself. (Rules for agents working *in a generated project* live in
`template/AGENTS.md`.)

## Layout
- `skills/ohmai/SKILL.md` is canonical; `skill` is a compatibility symlink. Details belong in `skills/ohmai/phases/`.
- `skills/ohmai/phases/NN-<id>.md` has one file per phase. The ids must match `cli/src/ohmai/phases.py`.
- `template/` is copied verbatim by `ohmai new`. Placeholders: `{{project_name}}`, `{{project_slug}}`,
  `{{idea}}`, `{{ohmai_version}}`, `{{date}}`, `{{uuid}}`. Docs that must be filled start with
  `<!-- ohmai:template ... -->`. `ohmai check docs:<file>` fails while the marker is present.
- `cli/src/ohmai/` is the CLI. Checks register with `@register(name, help)` in `checks/*.py` and return a
  `Result` (PASS/FAIL/WARN/SKIP/TODO).
- `template/parts/constraints.schema.json` is the single copy of the constraints schema.

## Dev loop
```bash
pip install -e "cli[dev]"
ohmai hooks install           # once per clone: git does not version hooks; `ohmai doctor` warns if missing
ruff check cli/src cli/tests && pytest cli/tests -q
ohmai new /tmp/demo --idea test && (cd /tmp/demo && ohmai check all)
```
Tests must not need KiCad: use `--netlist` / `opts["netlist"]` with a fixture netlist (see
`cli/tests/helpers.py`). The CI job with KiCad covers the kicad-cli paths and runs `ohmai known-bad` and
`ohmai golden`. See `docs/TESTING.md`.
- **New check = new known-bad case** (`tests/fixtures/known-bad/<case>/`, planted bug + clean twin) in the
  same commit; `test_known_bad.py` fails otherwise. If the check cannot catch it, fix the check.
- A golden diff is never "fixed" by accepting blindly: `ohmai golden --accept --reason "..."` only for an
  intended or explained change, in its own commit.
- Python deps: add to `cli/pyproject.toml`, pin in `cli/constraints.txt`, regenerate
  `cli/requirements.lock` with `scripts/pylock.py`. Optional tools (SKiDL, jlcparts) are not dependencies.

## Rules
- **Never invent facts** in docs, examples or tests that look real. The example constraints file
  (`TPS63001.example.yaml`) cites real datasheet pages. Keep it that way, or mark data as fictional.
- A stub must say so: return `TODO` with what is missing. Never return PASS for unimplemented work.
- Checks enforce design intent and never weaken it. No flags that silence ERC/DRC.
- Licensing: the core stays Apache-2.0. Call GPL/AGPL tools (KiCad, Freerouting, KiBot, and SKiDL, which
  imports GPL InSpice) as subprocesses or CI steps only. Do not import them or copy their code. New Python deps must be permissive (MIT/BSD/Apache).
- Keep docs crisp: tables and imperative steps, no marketing.

## Git strategy (this repo)
- `main` is always releasable. Work on short branches (`feat/…`, `fix/…`, `docs/…`) and merge via PR
  with green CI.
- Semver tags `vX.Y.Z`. Bump `cli/src/ohmai/__init__.py` in the release PR and note the change in
  `CHANGELOG.md`.
- The template and the skill are versioned with the CLI. Generated projects record the version in
  `ohmai.yaml`, `STATUS.md` and `.ohmai/phases/`. A breaking template change needs a major bump (from
  1.0) and a migration note.
- Commit per passing step. Never force-push `main`.
- Agent commits here carry the same `OhmAI-Author: name=...; kind=agent; vendor=...; model=...;
  instructions=...` trailer as project commits (see `docs/REVIEWS.md`). The generated-project rules and
  the hook live in `cli/src/ohmai/trailers.py`; keep the hook script POSIX sh (no ohmai/python needed).

## Anonymity & clean-repo hygiene (`anonymous: true` in `ohmai.yaml`)
- **Role labels only**: Refer to the human project owner as `owner` (never personal names, handles, emails, or street addresses) and reviewers by role (`reviewer-a`, `reviewer-b`; review files `reviews/<phase>/reviewer-a.md` and `reviews/<phase>/owner.md`).
- **Neutral trailers & git identity**: Use a GitHub `noreply` or generic bot email in git commits (never `@localhost`), and set `instructions=ohmai-skill <version>` in `OhmAI-Author:` trailers rather than internal runtime prompt names.
- **Repo-relative paths**: Never write machine-local absolute paths (`/Users/...`, `/home/...`, `/workspace/...`, `/tmp/...`) or local worktree/venv names in tracked files or commit messages.
- **Date-only & neutral prose**: Use `YYYY-MM-DD` dates without `HH:MM` timestamps, and paraphrase owner decisions in neutral specification prose instead of quoting chat messages verbatim.
- **No cross-project leakage**: Never reference unrelated projects or files outside this repository.

## Plugin release work
Read `INSTALL.md` and `docs/PLUGIN-PACKAGING.md`; package only the allowlisted guidance artifact. Run version/manifest/payload tests and the private-history audit before any approved public release. The compact optional host trigger is `docs/AGENT-NUDGE.md`.
