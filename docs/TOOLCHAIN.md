<!-- SPDX-License-Identifier: Apache-2.0 -->
# Toolchain on a shared box (restore, durability, coordination)

The toolchain is machine-local. KiCad comes from apt and ohmai runs from a venv, and neither is in project
git. On a shared agent box, a `/tmp` cleanup or two agents installing at once can wipe it. When that
happens `ohmai check toolchain` FAILs, or `kicad-cli` / `ohmai` disappear. Restore it from the recipe;
don't improvise.

## Restore (after any toolchain FAIL)
    scripts/restore-toolchain.sh          # run from the ohmai repo root
    source ~/ohmai-venv/env.sh
    cd <project> && ohmai check toolchain
The script is idempotent and does the following:
- Reads versions from the repo's `pinned-tools.yaml` (override KiCad with `KICAD_VERSION` if you must).
- Installs KiCad with apt as `kicad=$KICAD_VERSION*` (release reference 9.0.2; the exact version is
  checked). If the Debian HTTP proxy fails, it switches the sources to `https://deb.debian.org`
  (the originals are kept as `*.bak-ohmai`) and retries.
- Installs `poppler-utils` and `librsvg2-bin`.
- Creates the **durable** venv `~/ohmai-venv`, runs `pip install -e cli[dev,layout]`, writes
  `~/ohmai-venv/env.sh`, then runs `ohmai setup` (Freerouting 2.5.0 jar with sha256, easyeda2kicad
  1.0.1 with PyPI hashes, JDK 25 / KiCad checks). Setup does not touch `~/.config/freerouting` or a
  2.2.4 install.

Pins:
- OhmAI release pins (`pinned-tools.yaml`) are authoritative for *this ohmai version*. `ohmai doctor`
  FAILs on a mismatch; `OHMAI_ALLOW_UNPINNED=1` downgrades that to a warning.
- A project's `toolchain.lock.yaml` (tier 1) is authoritative for *that design*. Set `KICAD_VERSION`
  to match it when restoring a project that locked a specific KiCad build.
- jlcparts: a database, not a package. Keep it at `~/.cache/ohmai/jlcparts/cache.sqlite3` or set
  `OHMAI_JLCPARTS_DB`, never under `/tmp`.

## Rules
- **Do not assume `/tmp` survives.** No venv, database, render or checkout that matters lives there. The
  script refuses `OHMAI_VENV=/tmp/...`. Use `~/ohmai-venv` (shared) or a project `.venv`, and always
  `pip install -e`.
- **One agent restores, the others wait.** The script holds `flock ~/.cache/ohmai/toolchain.lock`, so a
  second agent blocks instead of running apt or pip in parallel. Don't run `apt-get` or `pip` around it.
- **Git is the only durable state.** Commit generated schematics (`hardware/*.kicad_sch`), `gen/layout/*.yaml`
  and `hardware/lib/` at the end of every step. `out/` is a rebuildable cache: regenerate it with
  `ohmai check`/`schematic build` and never rely on it as evidence across turns. Never rely on uncommitted
  local ohmai patches either: fold them into an ohmai commit first.
- After a restore, re-run the phase's checks from a clean `out/` before reporting.
