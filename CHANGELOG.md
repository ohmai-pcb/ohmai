# Changelog

## 0.1.0.dev0 (unreleased)
- `ohmai kicad-libs fetch` is now agent end-to-end, no owner steps: LCSC/EasyEDA via easyeda2kicad (LCSC number
  from bom.csv, title must match the MPN), `--url` direct manufacturer files, footprint upgrade to KiCad 9,
  auto datasheet pin check, `--pin-types` from the datasheet I/O column, footprint pad summary. Ultra
  Librarian / SnapEDA / vendor portals (`--source portal`) and HTML walls exit 75 (agent-retryable). The
  `pending-download` status and the human captcha handoff are removed.
- `ohmai kicad-libs fetch|import|verify|list <MPN>` (new): the supported library tool for parts the pinned KiCad
  libraries lack. `fetch` prints vendor (TI/Ultra Librarian/ST/ADI/Microchip/NXP/SnapEDA) download steps and
  records `pending-download`; `import` takes a vetted zip/dir/.kicad_sym/.kicad_mod, merges into
  `hardware/lib/OhmAI_Project.*`, adds `${KIPRJMOD}` table rows (backed up) and writes provenance to
  `hardware/lib/manifest.yaml`; `verify` checks every symbol pin number+name against the datasheet pin table;
  `--status provisional` registers a stopgap. Skill phases 4/7 and SKILL.md: agents run the tool, never
  hand-write one-off symbol scripts. `lib-refs` hint points at the tool.
- `ohmai check lib-refs` (phase 7, new) and `lib-refs-strict` (phases 8 and 10): every schematic `lib_id` and every
  schematic/BOM footprint must resolve through the project `sym-lib-table` / `fp-lib-table` (symbols FAIL;
  footprints WARN in phase 7, FAIL when strict). SKIP with no references or with the pinned libraries not
  installed. `ohmai kicad-libs tables` now keeps project-local `${KIPRJMOD}` rows. Skill: missing KiCad parts are
  a tool problem; vetted import path in phases/04-parts.md (stock by land pattern, manufacturer/Ultra Librarian,
  datasheet-verified symbol generator as a marked interim).
- `ohmai check escape-room` (phase 8, new): the 7 escape-room placement rules around fine-pitch QFNs (toe gap,
  flash facing, edge lanes, ring vias before routing, inductor facing, ring fill <= 60 %, per-side capacity),
  configured in `hardware/layout.yaml` `escape_room:`; SKIP without that section. `ohmai layout fit` (new):
  would a part fit at x,y,rot, nearest free spots (`--scan`), rules after the move (`--rules`). Plain Python on
  the .kicad_pcb (ohmai.layout.placement), no KiCad. Known-bad case `escape-room-toe-gap`. docs/LAYOUT.md.
- `ohmai parts search` reads the current jlcparts database format (`meta.format = source-db-v2`, table
  `jlc_components`, jlcparts/sourceDb.py): base/expand/preferred, "1-99:0.03,100-:0.02" price ladder,
  `fetched_at` as the snapshot date, delisted (`present = 0`) parts left out. The old partLib schema still works;
  the result meta names the format. Before, a current snapshot was rejected as "not a supported" database.
- Known-bad case `footprints-missing-pad` (fictional project-library footprint without pad 3): the `footprints`
  check had none, so `test_every_check_has_a_planted_bug` failed. Fixed a NameError in the same check: a footprint
  with a pad that is not on the symbol crashed it (WARN was not imported) instead of WARNing.
- `ohmai doctor` (new): environment self-check; WARNs when the commit-msg hook is missing, outdated or not
  executable (INFO on CI clones), and when kicad-cli is missing; `--strict` exits 1 on warnings. `ohmai hooks
  install|status` now also works in a plain git repo (the OhmAI repo itself), not only in a project. The OhmAI
  repo had no hook installed, so its trailer rule was never enforced locally; AGENTS.md dev loop installs it.
- Toolchain: the Freerouting tier-1 entry records its Java runtime (`java:`; `FREEROUTING_JAVA` or `java` on
  PATH) and `check toolchain` FAILs on a different runtime (Freerouting >= 2.2 needs Java 25). Release jars say
  `Implementation-Version: unspecified`, which was locked as the version; the file name is used then. Found
  while moving pico-k from 2.1.0 to 2.2.4 (pico-k D-019). TESTING/ARCHITECTURE updated.
- Docs: new docs/TESTING.md (unit, known-bad, golden, lock/upgrade, canary); ARCHITECTURE.md gains the
  licensing table and "research tools: what OhmAI uses, in which phase, and why" (atopile, tscircuit, KiCad
  MCP servers and commercial tools not used, with reasons); ROADMAP, SKILL.md cheat sheet (lock, upgrade,
  kicad-libs, schematic build, parts search, golden, known-bad), AGENTS.md (new check = new known-bad case;
  golden accept rules; dep pinning), README layout, template ohmai.yaml lock comment.
- `ohmai parts search` (phase 4, optional): reads a local jlcparts `cache.sqlite3` (OHMAI_JLCPARTS_DB/--db,
  read-only; schema of jlcparts/partLib.py), ANDed terms plus category/package/basic/min-stock filters,
  unit price at --qty from the snapshot price ladder, LCSC URL per hit. Results are labelled snapshot
  data; phase 04 guide says to copy live values from the page. Tested on a FICTIONAL fixture DB only.
- New check `skidl-netlist` (phase 7, optional): SKiDL builds an independent netlist from expected_nets.csv,
  loading each part from the pinned KiCad symbol libraries, and it is compared with the KiCad export as net
  partitions (split, short, extra, nc; open rows allowed). WARN when SKiDL is not installed. SKiDL runs only
  as a separate process (`ohmai/_skidl_runner.py`, `$OHMAI_SKIDL_PYTHON`): SKiDL 2.3.0 imports InSpice
  (GPL-3.0-or-later) at import time. Known-bad cases: skidl-netlist-label-short, skidl-netlist-no-such-pin
  (`needs: [skidl]`, skipped without it).
- Toolchain canary (owner 2026-10-15): `scripts/canary.py [--libs match-app|latest|TAG] [--kicad-cli P]`
  runs the known-bad + golden suites against the newest KiCad app/libraries and writes a markdown report
  (also to `$GITHUB_STEP_SUMMARY`); it never writes a lock, snapshot or ohmai.yaml. `.github/workflows/canary.yml`
  (weekly + manual; report only, `continue-on-error`): kicad/kicad:9.0 with the newest 9.0.x libraries, and
  kicad/kicad:latest with the newest libraries. The tool CI's KiCad job now pins kicad/kicad:9.0.2 (the golden
  snapshots' build) and runs `ohmai known-bad` and `ohmai golden`. First local canary run (libs 9.0.8)
  found an environment-dependent fixture (toolchain-kicad-drift hard-coded the libs tag); known-bad opts
  now accept `@pinned-libs`.
- `ohmai upgrade <tool> <version> [--path P] [--install] [--explain WHY] [--dry-run] [--no-commit]`
  (owner 2026-10-15): selects the candidate (KICAD_CLI / OHMAI_KICAD_LIBS_TAG / FREEROUTING_JAR / ...), runs
  the known-bad and golden suites in a subprocess with it, prints the golden diffs, and moves the tier-1 pin
  only when both are clean or `--explain` covers the diffs or the lack of suite coverage (freerouting, KiKit,
  KiBot, iBOM, kipy, SKiDL, jlcparts have none yet). A known-bad miss always blocks. kicad-app stays inside
  ohmai.yaml `kicad.app`; kicad-libs also moves `kicad.libraries` and rewrites the lib tables. Lock history
  records from/to, suite results and the explanation; the commit contains only those files.
- Golden projects (owner 2026-10-15): `ohmai golden [name...] [--diff] [--accept --reason R]` rebuilds each
  `tests/golden/<name>/` from its sources in a temp dir and diffs normalised netlist, ERC, BOM (from the
  netlist), embedded library-symbol digests and check results against `snapshots/`; drc and gerbers artefacts
  exist for goldens with a layout. A difference fails until accepted; `--accept` needs a reason and logs it,
  the changed artefacts and the toolchain in `snapshots/MANIFEST.yaml`. First golden: ldo-led (no layout yet).
  `source:` + `ref:` mode builds a project repo from `git archive` (pico-k, once frozen; documented, not added).
- Known-bad suite (mutation fixtures, owner 2026-10-15): `tests/fixtures/known-bad/<case>/case.yaml`, one planted
  mistake per check plus a clean twin (`ohmai known-bad [case...] [--json]`, `ohmai.knownbad`, pytest
  `test_known_bad.py`; a coverage test requires a case for every registered check). 30 cases over 27 checks;
  `creepage` has none (TODO check). Shared bases: the golden ldo-led sources, its netlist as YAML
  (`ohmai.testkit`), its built sheets, fictional I2C parts. Found and fixed: `bom-lock` did not notice a stale
  BOM; it now compares bom.csv with the schematic netlist (refs, MPN/approved alternates, value, footprint,
  DNP). `questions` reports its planted question as WARN by design. Netlist reader keeps footprint and
  valueless properties (dnp, exclude_from_bom). Wheel ships `tests/` as `ohmai/_data/tests`.
- Toolchain lock, two tiers (owner 2026-10-15): `ohmai lock` writes `toolchain.lock.yaml` (tier 1 exact:
  kicad-cli, KiCad libraries with a content hash, Freerouting, KiKit, KiBot, InteractiveHtmlBom, ohmai's Python
  deps with RECORD hashes; tier 2 ranges: Python, ohmai, git, rsvg-convert); refuses a changed tier-1 tool
  (`ohmai upgrade`). New check `toolchain` (phases 7, 8, 11): FAIL on a tier-1 mismatch, missing locked tool or
  portable hash mismatch, WARN on a machine-specific hash or a tier-2 helper out of range, WARN without a lock.
  `kicad-app` and `kicad-libs` read the same lock entries (one implementation); `kicad.app`/`kicad.libraries`
  stay the policy, the lock must lie inside it. `ohmai lock --constraints` prints the locked Python pins (template
  CI installs them). Tool repo: `cli/constraints.txt` (exact pins, CI installs with `-c`) and the hash-checked
  `cli/requirements.lock` from `scripts/pylock.py`; `version_in_range` moved to `ohmai.versions`.
- Initial skeleton: skill + 12 phase guides, project template, `ohmai` CLI (`new`, `status`, `check`,
  `release`, `skill`), constraints JSON schema with an TPS63001 example, CI for the tool and the template.
- Dogfood fixes (pico-k): `ohmai new` prints an absolute, quoted `cd` path; skill states the page and
  price citation conventions and the `PROPOSAL:` marker; phase 1 guide says a check PASS is not a lock.
- New `questions` check in phases 1-3: WARN while STATUS.md has open questions not marked "Non-blocking".
- Target markets are a required phase 1 question: STATUS.md front matter `markets:` (e.g. `[EU, UK, US]`),
  a target-markets table in `docs/requirements.md`; the `questions` check WARNs while `markets:` is empty.
- User instruction manual as a standard deliverable: `template/docs/manual.md` (sections and the EU rules
  they serve: GPSR (EU) 2023/988 Art. 9(5)-(7), EMC 2014/30/EU Art. 7, 15-18, RoHS 2011/65/EU Art. 7,
  WEEE 2012/19/EU Art. 14 / Annex IX, CE per Reg. (EC) 765/2008 Art. 30, RED/LVD scope notes).
  STATUS.md "Manual warnings" (`MW-xx`); safety plan sections "Target markets and legislation" and
  "Required manual warnings"; phase 2/3/12 guides updated.
- New checks: `manual-warnings` (phases 2-3: every MW-xx named in architecture/safety is registered in
  STATUS.md and listed in the safety plan) and `manual` (phase 12: sections, per-market rules for EU and
  rough UK/US rules, every MW-xx referenced; unknown markets WARN for manual review).
- Independent review gates (owner request 2026-10-15), tool-agnostic: `ohmai.yaml` → `reviews.gates`
  (defaults for safety, constraints, schematic, layout, bom, bringup); `ohmai review start <phase>` writes
  `reviews/<phase>/BRIEF.md` (scope + sha256 content hash, sources, locked decisions, checklist, output
  format, independence priority) and `resolutions.md`; new check `review` / `review:<phase>` (in the phase
  check lists) FAILs on a missing review, reviewer == author (STATUS `authors:` / `OhmAI-Author:` trailers),
  files changed after review, or an unresolved issue; WARNs on independence below the minimum. Skill
  section 3a, phase guides, template (ohmai.yaml, STATUS.md `authors:`, AGENTS.md, PR template, CI note),
  `docs/REVIEWS.md` with the GitHub mapping. Dogfood fix (pico-k): only the findings table (with `File` and
  `Verdict` columns) is parsed; an identical copy saved at a second accepted path counts once.
- `ohmai upgrade-guides [--dry-run] [--add-missing]`: re-pin `.ohmai/phases/` to the installed version,
  bump `ohmai_version` in STATUS.md, optionally copy template docs the project lacks (never overwrites).
- Review gate: reviewer-requested post-review nits. A reviewer's own `[nit]` issue resolved as
  `fixed-post-review` + commit lets the files named in it differ from the reviewed `files_hash` (WARN, no
  re-verification); any other changed file still FAILs. Briefs keep earlier per-file hashes under
  `history:` so a regenerated brief does not lose the reviewed baseline (dogfood: pico-k Reviewer-A R1).
- Review gate (dogfood: pico-k Reviewer-A H3): the brief's "Changed since" list is computed against the brief the
  latest reviewer checked; free-form `OhmAI-Author:` trailers no longer add a duplicate name-only author when
  a full identity with that name exists; setting `review.status: reviewed` in a constraints YAML after the
  review no longer invalidates the review hash (compared without the `review:` block, via git history).
- Commit trailers enforced (owner request 2026-10-15): `ohmai check commits` (every phase, and inside
  `review:<phase>`) FAILs on a commit since the merge-base with `main` without a well-formed
  `OhmAI-Author: name=...; kind=agent|human; vendor=...; model=...; instructions=...` trailer
  (kind=human needs only name); commits reachable from `commits.trailer_required_since` only WARN, so
  history is never rewritten. POSIX commit-msg hook installed by `ohmai new` and `ohmai hooks install`;
  `ohmai commit -m ...` appends the trailer from `ohmai.yaml` `author:` / `OHMAI_AUTHOR_*`. The review
  gate takes authors from trailers first; STATUS.md `authors:` only for grandfathered commits. `ohmai new`'s
  initial commit carries a trailer.
- Phase 7 (dogfood: pico-k D-012): pinned official KiCad libraries + per-project lib tables
  (`ohmai kicad-libs install|tables|status`, check `kicad-libs`); per-part symbol maps
  `parts/symbol-maps/<stem>.yaml` (pin-map, passives and pull-ups work in datasheet pins; stale maps FAIL);
  constraints bind by MPN field or `match.refs`, not value substring; passives: rail decoupling vs per-pin,
  nominal-only is not a maximum, neighbour nets through series parts; the review gate is reported apart from
  RESULT (`--gate` to include). Datasheet-figure schematics: `ohmai schematic build` draws
  `hardware/gen/layout/*.yaml` with real orthogonal wires (wires split at every pin/tee so KiCad connects
  them); new `readability` check (label-only passive joins, local labels with allow-list, crossings,
  junction ambiguity, overlapping text/symbols, wires through bodies, off-grid). `netlist` accepts a
  wired, unlabelled `Net-(...)` net whose nodes match.
- `readability` (dogfood: pico-k Reviewer-A schematic review F1, F13-F15): FAILs when a power symbol's net name sits
  next to another node's wire or junction, a field sits on a pin line or pin number or over a DNP cross, a part's
  text touches a power symbol's name, or the sheet title overflows the title-block field (font ESTIMATE).
- `kicad-app` check (phases 7, 8, 11; owner request, pico-k D-014): `kicad-cli version` must be inside ohmai.yaml
  `kicad.app` (`">=9.0.2,<9.1"`, `"9.0.x"`); FAIL outside the range or on an unparseable range, WARN when unpinned.
  Template pins `">=9.0,<9.1"` next to `kicad.libraries`.
- Review independence wording: the WARN and the brief now say "level N or better (1 = most independent)" instead
  of "below the configured minimum" (the logic was right: a larger number is less independent).
- Review gates: `require_kind: human` (owner request, pico-k 2026-10-15): `ohmai check review:<phase>` FAILs unless
  a valid review (current hash, full coverage, not an author identity) has `kind: human`; the owner may sign when
  agents authored the work. Template and default gates set it for `bringup`; safety unchanged. Brief, REVIEWS.md,
  SKILL.md, phase 12 guide, tests.
- Review gates: owner independence waiver (last resort). The agent first tries every better level (vendor > model
  > bot/instructions > human) and documents why each was unavailable, then asks the owner. `resolutions.md`
  `accepted_independence: [{gate, reviewer, level, files_hash, by: owner, owner, reason, alternatives_tried, ref}]`
  turns the independence WARN into PASS with a "WAIVED" note for that one review/gate/hash; an entry missing a
  field (e.g. alternatives_tried) FAILs; it never carries over to a new hash or another gate. Dogfood: pico-k D-016.

