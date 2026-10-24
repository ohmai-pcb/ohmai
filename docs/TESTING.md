# Testing OhmAI

OhmAI's checks gate real hardware, so they are tested the way a design would be. Four layers, all run by
the tool CI. Unit tests never need KiCad; anything that needs kicad-cli, the pinned libraries, git or SKiDL
is skipped (not passed) when it is missing.

| Layer | Command | Proves | Where |
|---|---|---|---|
| Unit tests | `pytest cli/tests -q` | the code does what it says | `cli/tests/` |
| Known-bad (mutation) suite | `ohmai known-bad [case...]` | every check FAILs on a planted mistake and PASSes on its clean twin | `tests/fixtures/known-bad/` |
| Golden projects | `ohmai golden [name...] [--diff]` | reference projects rebuild to approved outputs | `tests/golden/` |
| Toolchain lock + upgrade | `ohmai lock`, `ohmai check toolchain`, `ohmai upgrade` | results are made with the locked tools; a pin moves only after the suites pass | project `toolchain.lock.yaml` |
| Canary | `scripts/canary.py`, `.github/workflows/canary.yml` | the newest KiCad/libraries would not break us (reports only) | weekly CI |

## Known-bad suite
Each `tests/fixtures/known-bad/<case>/case.yaml` names one check and one planted bug. The runner builds two
projects from the same bases (`golden:<name>` = a golden project's sources, `_base/<name>/` = shared files):
the **clean twin** and the **bad twin** (= clean + the `mutate:` edits). A case passes only when:
- the clean twin gets the expected clean status (PASS; WARN only where the check WARNs by design, with
  `why_clean_warn`),
- the bad twin gets FAIL (or WARN by design, with `why_bad_warn`), and
- the bad result matches `detail:` (a regex), so the check failed for the planted reason, not by accident.

A fixture whose edit target is missing is an error, never a pass. `test_known_bad.py` requires a case for
every registered check (or an entry in `ohmai.knownbad.NOT_COVERED` with a reason) and checks that the
`_base/` copies equal a fresh build of the golden project. Format: the docstring of `ohmai/knownbad.py`.
Fictional parts are marked FICTIONAL / `FICT-`.

Adding a check: add a case in the same commit. A check that cannot catch its case is a bug in the check.
Fix the check; never weaken the fixture.

## Golden projects
`tests/golden/<name>/golden.yaml` says how to rebuild the project (`src/` overlaid on `ohmai new`, or
`source:` + `ref:` for a project repo at a frozen git ref) and which artefacts to compare:

| Artefact | Content (normalised: no dates, tool banners, absolute paths, marker coordinates) |
|---|---|
| `netlist` | components (ref, value, symbol, footprint, MPN, flags) and nets with pin names |
| `erc` | ERC violations by sheet, type and items |
| `bom` | BOM grouped from the netlist (refs, qty, value, footprint, MPN, DNP) |
| `symbols` | digest of every embedded library symbol (a library change shows up here) |
| `checks` | status + summary of the listed checks |
| `drc`, `gerbers` | DRC items; fab file digests without date lines (goldens with a layout; none yet) |

Any difference fails. Read `ohmai golden <name> --diff`. If the change is intended or explained, accept
it: `ohmai golden <name> --accept --reason "why"`. That writes `snapshots/*.txt` and logs the reason, the
changed artefacts and the toolchain in `snapshots/MANIFEST.yaml`. Commit snapshots on their own.

Goldens today: **ldo-led** (header → AP2112K-3.3 → 3V3 → R + LED, two sheets, no layout). **pico-k**
becomes a golden by reference once its owner freezes a revision (`tests/golden/README.md`). It is never
copied or modified.

## Toolchain lock and upgrades
`ohmai lock` writes the project's `toolchain.lock.yaml`.
- Tier 1 is exact and FAILs on a mismatch: kicad-cli, KiCad libraries (content hash), Freerouting, KiKit,
  KiBot, InteractiveHtmlBom, kicad-python, SKiDL, jlcparts (package or database), and ohmai's Python deps.
- Freerouting's entry also records the Java runtime that runs the jar (`java`, from `FREEROUTING_JAVA` or
  `java` on PATH); a different runtime FAILs. The version comes from the jar manifest, or from the file name
  when the manifest says `unspecified` (all release jars 2.1.0 ... 2.5.0).
- Tier 2 helpers use ranges and WARN when out of range: Python, ohmai, git, rsvg-convert.

`ohmai check toolchain` verifies it in phases 7, 8 and 11, and `kicad-app`/`kicad-libs` read the same
entries. The tool repo pins its own Python deps in `cli/constraints.txt` (CI installs with `-c`) and the
hash-checked `cli/requirements.lock` (`scripts/pylock.py`).

`ohmai upgrade <tool> <version>` is the only way a tier-1 pin moves:
1. It selects the candidate: `--path` for kicad-app/freerouting/ibom/jlcparts, the tag for kicad-libs
   (`--install` downloads it), or the installed version for Python packages.
2. It runs the known-bad and golden suites with that candidate and prints the golden diffs.
3. It moves the pin only if both are clean, or if `--explain` covers the golden diffs or the missing suite
   coverage. Freerouting, KiKit, KiBot, iBOM, kipy, SKiDL and jlcparts have no golden yet, so they always
   need an explanation. A known-bad miss always blocks.
   Freerouting: no suite routes a board yet, so the explanation must cite a same-DSN comparison of the old
   and new version on a real project (unrouted count, DRC after import) until a routed golden exists.
4. It records the change in the lock history and commits only the lock (plus `kicad.libraries` and the
   lib tables for kicad-libs) as its own commit.

## Canary
`scripts/canary.py` picks the newest stable library tag for the installed kicad-cli's series
(`--libs match-app`, the default), or the newest overall (`--libs latest`), or a given tag. It installs the
libraries into the cache, runs both suites with the candidate selected, and writes a markdown report
(also to `$GITHUB_STEP_SUMMARY`).
- Exit codes: 0 clean, 1 breakage, 2 setup problem or golden skipped.
- It never writes a lock, snapshot or ohmai.yaml.

`.github/workflows/canary.yml` runs it weekly and on demand, in two configurations:
- kicad/kicad:9.0 with match-app (the current series);
- kicad/kicad:latest with latest (the next series).

It is report-only (`continue-on-error`). A red canary means "the next upgrade needs work". To adopt a
candidate, run `ohmai upgrade` in a project. The tool CI's KiCad job stays pinned to the build the golden
snapshots were made with (kicad/kicad:9.0.2).

The first local run against libraries 9.0.8 found a fixture that hard-coded the library tag
(toolchain-kicad-drift). Fixtures now write `@pinned-libs` instead.

## Optional tools in tests
- SKiDL (`skidl-netlist`): the cases declare `needs: [skidl]` and are skipped without it.
  `test_skidlnet.py` tests the comparison without SKiDL and asserts SKiDL is never imported into the
  ohmai process.
- jlcparts (`ohmai parts search`): tested on FICTIONAL databases in both jlcparts schemas (current
  `source-db-v2` from jlcparts/sourceDb.py and the older partLib one). A real `cache.sqlite3` is not needed in CI.
