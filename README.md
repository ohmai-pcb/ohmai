<!-- SPDX-License-Identifier: Apache-2.0 -->
# ⚡ OhmAI

**Turn a one-line hardware idea into a datasheet-verified, DRC-clean KiCad 9 project — with an AI agent doing the legwork and you in the driver's seat.**

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![KiCad 9](https://img.shields.io/badge/KiCad-9.0+-314CB0.svg)](https://www.kicad.org/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB.svg)](https://www.python.org/)
[![Status: v0.1 Experimental](https://img.shields.io/badge/Status-v0.1_Experimental-orange.svg)](#what-works-today-vs-current-limitations)

[**Quickstart**](#quickstart) · [**How It Works (The 12 Phases)**](#the-12-phases) · [**What Works Today**](#what-works-today-vs-current-limitations) · [**CLI Reference**](#cli-overview) · [**Pinned tools**](#pinned-tool-versions-per-release)

---

### What is OhmAI?

Ask a raw LLM to design a PCB and it will happily hallucinate pin numbers, invent passive values, pick out-of-stock single-source parts, or declare victory with 40 unconnected nets.

**OhmAI** fixes that by giving your coding agent (Cursor, Claude Code, Codex, or any LLM with shell access) a **12-phase hardware engineering harness (`skill/`)** backed by a **deterministic verification CLI (`ohmai`)** for **KiCad 9**:

```bash
ohmai new ~/hw/my-board --anonymous --idea "A solar-powered USB-C charger for 2 phones"
cd ~/hw/my-board && ohmai status
```

Then tell your agent:
> *"My idea is a solar-powered USB-C charger for 2 phones — use OhmAI to make it."*

Your agent researches parts, extracts pin tables and layout rules with exact datasheet page citations, generates hierarchical KiCad 9 schematics, cross-checks multi-source BOM alternates against JLCPCB/LCSC stock, runs `kicad-cli` ERC/DRC and copper-pour checks, and prepares fabrication outputs and bring-up self-test code — pausing at every gate for **independent review** and **your sign-off**.

---

### ✨ Highlights

- 📄 **Zero-Guesswork Datasheet Citations** — Before a single wire is drawn, every IC and connector gets a structured `parts/constraints/<PART>.yaml` where every pin, direction, decoupling rule, and keepout cites an exact PDF page verified against `datasheets/manifest.yaml`.
- 🔍 **Deterministic KiCad 9 & Netlist Checks** — `ohmai check <phase> --gate` wraps `kicad-cli` (ERC, DRC, Gerber/drill/3D export), diffs your schematic netlist against `expected_nets.csv`, and verifies voltage dividers, capacitor DC-bias derating, I²C pull-ups, critical switching loops, and copper pour neck widths.
- 🛡️ **3-Rule BOM Sourcing & `jlcparts` Verification** — Automatically enforces multi-manufacturer drop-in alternates, cross-checks every LCSC `C#` code and package against an offline `jlcparts` database, and flags any single-source component until rejected alternatives and an owner decision (`D-xxx`) are documented.
- 🤝 **Multi-Agent Independent Review Gates** — Built-in review briefs (`ohmai review start <phase>`) require a separate reviewer identity (a second model, reviewer agent, or human engineer — never the authoring session) locked to a cryptographic `files_hash`.
- 🧪 **Ready-to-Run Bring-Up Code & Compliance Docs** — Generates pre-board bench test plans, target-market safety/compliance manuals (`CE` / `FCC` / `WEEE`), and self-test firmware verified against `hardware/gen/pinmap.yaml` before you ever power on a board.

---

Plugin/skill installation for Cursor, Grok Build, Grok Bot and Claude Code: [INSTALL.md](INSTALL.md). The prepared 0.1.1 plugin is guidance only; external tools are separate.

## Why OhmAI exists

Left to their own devices on a PCB project, LLM agents tend to guess pin numbers, invent passive values, pick single-source parts that are out of stock, or declare victory while DRC has dozens of unconnected nets.

OhmAI does **not** make an LLM an autonomous senior hardware engineer. Instead, it constrains the agent inside a 12-phase gated workflow where claims must be backed by deterministic CLI checks and cited datasheet pages, and where the human owner makes every architectural, component, and waiver decision:

- **The owner decides; agents do the legwork.** Nothing locks without human sign-off (`docs/decisions.md` + `STATUS.md`). In practice, the human owner still steers trade-offs, catches bad physical assumptions, and decides when a layout or sourcing compromise is acceptable.
- **Datasheet-cited constraints (`parts/constraints/<PART>.yaml`).** Before drawing a schematic, every IC and connector gets a structured YAML file listing pins, directions, decoupling rules, and layout constraints — each citing a page number verified against `datasheets/manifest.yaml`. Pin directions are never guessed.
- **Deterministic checks (`ohmai check <phase> --gate`).** Wraps `kicad-cli` (sheet load, ERC, DRC, Gerber/drill/3D export), diffs the schematic netlist against `expected_nets.csv`, checks pin maps, regulator outputs, passive values, I²C pull-ups, copper pour continuity, and BOM-to-fab parity.
- **Independent review gates (`ohmai review start <phase>`).** Line-by-line review gates (`safety`, `constraints`, `schematic`, `layout`, `bom`, `fab`, `bringup`) require a separate reviewer identity (another model/vendor, a separate reviewer agent, or a human — never the authoring session) bound to a SHA-256 `files_hash`.
- **Git is the memory.** Approved state, open questions, review resolutions, and waivers live in the repository so any agent or human can inspect or resume the project.

---

## What works today vs. current limitations

> **Status: v0.1 (experimental / early-stage).** Internal reference-board validation has reached fab dry-run and bring-up preparation; no public example board or physical fabrication is claimed. See [ROADMAP](docs/ROADMAP.md).

### Works well in v0.1
- **Datasheet constraint extraction & citation checking (`ohmai check constraints`, `ohmai datasheets`):** Catching pinout/direction mistakes, missing pull-ups, and unverified datasheet claims before schematic capture begins. Tracking datasheet URLs, SHA-256 digests, and cited pages in `datasheets/manifest.yaml` without committing binary `.pdf` blobs.
- **Schematic, netlist, and BOM parity (`ohmai check schematic`, `ohmai check bom`):** Enforcing 1:1 parity across `expected_nets.csv`, `hardware/*.kicad_sch`, `hardware/*.kicad_pcb`, `hardware/gen/pinmap.yaml`, and `bom/bom.csv`.
- **3-rule BOM sourcing & `jlcparts` verification:** Requiring multi-manufacturer alternates for passives and active parts (or an explicit `single-source:` justification listing `checked:` rejected candidates + owner decision `D-xxx`), cross-checked against a local `jlcparts` SQLite database (`OHMAI_JLCPARTS_DB`).
- **Pre-board bring-up self-test & manual checks (`ohmai check bringup-code`, `ohmai check manual`):** Verifying that bring-up test scripts use the exact GPIO assignments in `hardware/gen/pinmap.yaml` and that `docs/manual.md` covers all `MW-xx` warnings and target-market compliance sections.

### Still rough / requires heavy human steering
- **PCB placement and routing (`ohmai layout`):** The 4-stage layout pipeline (`fit`, `fanout`, `autoroute` via Freerouting, `handroute`, `pour`) and `ohmai check placement` (`critical_loops` and `keepouts`) can close a dense board to `0` DRC violations and `0` unconnected nets, **but the resulting copper looks like an autorouter output plus scripted patch routes — not a clean, human-crafted PCB layout.** Closing tight boards in v0.1 required human guidance, iterative placement adjustments, passive package swaps (`0603` → `0402`), and explicit owner waivers (such as allowing signal hops on an internal power plane).
- **Reference-level complexity so far:** v0.1 has been exercised on datasheet-application-note circuits (MCU + buck-boost SMPS + SPI sensor front end), not high-speed impedance-controlled, RF, or high-voltage designs.

---

## The 12 Phases

| # | Phase (`<id>`) | Outputs | Automated Checks & Review Gate | Gate (Human Owner) |
|---|---|---|---|---|
| 1 | `requirements` | `docs/requirements.md` (`R-xx` specs, tolerances, markets, size/cost targets) | `status`, `questions`, `commits` | Locks the spec |
| 2 | `architecture` | `docs/architecture.md` (options, block diagram, trade study) | `status`, `questions`, `commits` | Picks an option |
| 3 | `safety` | `docs/safety.md` (applicable directives per market, `MW-xx` manual warnings) | `safety`, `review:safety` | Approves compliance plan before parts freeze |
| 4 | `parts` | `parts/selection.md`, initial `bom/bom.csv`, `datasheets/manifest.yaml` | `bom` (3-rule sourcing & `jlcparts`), `datasheets` | Approves parts & alternates |
| 5 | `constraints` | `parts/constraints/<PART>.yaml` + `expected_nets.csv` | `constraints` (page citations, pin tables), `review:constraints` | Approves after independent review |
| 6 | `rfq` | `parts/rfq/` supplier questions / RFQs (or N/A) | `status`, `questions`, `commits` | Sends RFQs (or marks N/A) |
| 7 | `schematic` | Hierarchical KiCad 9 schematics (`hardware/*.kicad_sch`), `hardware/gen/pinmap.yaml` | `toolchain`, `kicad-libs`, `sheet-load`, `erc`, `netlist-diff`, `pinmap`, `power`, `passives`, `buses`, `readability`, `renders`, `review:schematic` | Reviews schematic diff, renders & netlist parity |
| 8 | `layout` | `hardware/<project>.kicad_pcb`, 2D/3D renders, `docs/layout/checklist.md` | `drc`, `placement` (`critical_loops` & `keepouts`), `renders`, `models-3d`, `review:layout` | Reviews 2D/3D layout, pour continuity & waivers |
| 9 | `enclosure` | `docs/enclosure.md`, exported board `.step` (or N/A) | `status`, `questions`, `commits` | Approves mechanical fit (or N/A) |
| 10 | `bom` | Locked `bom/bom.csv`, optional `bom/fab-actual.csv` diff | `bom`, `review:bom` | Locks the BOM |
| 11 | `fab` | Gerbers, drills, PnP (`cpl.csv`), assembly BOM (`out/fab/<rev>/`) | `fab` (re-runs 7+8+10 + Gerber/drill/CPL export), `review:fab` (`require_kind: human`) | Human pre-order sign-off; tags `rev-A` |
| 12 | `bringup` | `test/bringup-plan.md`, `test/firmware/bringup_selftest.*`, `docs/manual.md` | `bringup-code`, `manual`, `review:bringup` (`require_kind: human`) | Human sign-off on real hardware measurements |

Details: [`skill/phases/`](skill/phases/), [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), and [`docs/REVIEWS.md`](docs/REVIEWS.md).

---

## Quickstart

### Requirements
- **Python ≥ 3.10**, **git**, and **KiCad 9.0.x** (`kicad-cli` on `PATH`; this release was tested with 9.0.2). KiCad checks report `SKIP` without it.
- Shared or ephemeral agent boxes: restore with `scripts/restore-toolchain.sh` (pinned KiCad + durable `~/ohmai-venv` + `ohmai setup`, HTTPS-mirror fallback, single-installer lock); see [docs/TOOLCHAIN.md](docs/TOOLCHAIN.md).
- External tools this release pins (see [Pinned tool versions per release](#pinned-tool-versions-per-release)): Freerouting 2.5.0 (jar + JDK 25) and easyeda2kicad 1.0.1, plus optional `rsvg-convert` (`librsvg2-bin`) and a local `jlcparts` SQLite database (`OHMAI_JLCPARTS_DB` or `~/.cache/ohmai/jlcparts/cache.sqlite3`).

### Install & Create a Project
```bash
pip install "git+https://github.com/ohmai-pcb/ohmai.git@v0.1.0#subdirectory=cli"
# contributors: git clone https://github.com/ohmai-pcb/ohmai.git && cd ohmai && pip install -e "cli[dev,layout]"
ohmai setup                            # install this release's pinned tools (no prompts)
ohmai skill install                    # copies skill/ to ~/.cursor/skills/ohmai (use --dest for other agents)

# Create a new project (add --anonymous for clean public repos using role labels & repo-relative paths)
ohmai new ~/hw/my-board --anonymous --idea "A solar-powered USB-C charger for 2 phones"
cd ~/hw/my-board && ohmai status && ohmai check current
```

Then tell your agent: *"My idea is a solar-powered USB-C charger for 2 phones, use OhmAI to make it."* The agent follows [`skill/SKILL.md`](skill/SKILL.md).

---

## CLI Overview

| Command | Purpose |
|---|---|
| `ohmai new <path> --idea "..." [--anonymous]` | Initialize a project repo from `template/`, pin `.ohmai/phases/`, and install git hooks |
| `ohmai status [--set-phase N --gate G --decision D-xxx]` | Show current phase, gate status, locked decisions, and open questions; set phase/gate in STATUS.md front matter |
| `ohmai check [current\|<phase>\|<check>] [--gate]` | Run deterministic checks (`--list` to view all checks); writes `out/summary.md` |
| `ohmai review start <phase>` | Generate `reviews/<phase>/BRIEF.md` (with SHA-256 `files_hash`) and `resolutions.md` |
| `ohmai layout {fit,fanout,autoroute,pour,handroute}` | Run the 4-stage PCB layout helpers (`critical_loops`, `keepouts`, pour neck width) |
| `ohmai passives swap <REF> --package <0402\|0603\|0805>` | Swap a passive package across schematic/layout specs while keeping BOM parity |
| `ohmai datasheets {manifest,fetch,check}` | Manage `datasheets/manifest.yaml` (SHA-256 digests + cited pages) and fetch PDFs on demand |
| `ohmai kicad-libs {install,tables,models,status}` | Manage pinned KiCad libraries and fetch/generate project-scoped 3D `.step` models |
| `ohmai kicad-libs {fetch,import,verify,list} <MPN>` | Agent-only fetch/import of parts the pinned libraries lack (LCSC/EasyEDA via easyeda2kicad, direct manufacturer URL) into `hardware/lib/` + lib tables + `manifest.yaml`, datasheet pin check and pin types; walled sources exit 75 |
| `ohmai commit -m "..." [-a]` / `ohmai hooks {install,status}` | Commit with `OhmAI-Author:` trailers and manage `commit-msg` / `pre-push` hooks |
| `ohmai setup` / `ohmai doctor` | Install and verify this release's pinned tools (`pinned-tools.yaml`); doctor also checks git hooks |
| `ohmai lock` / `ohmai upgrade` / `ohmai release <tag>` | Manage `toolchain.lock.yaml` or build a fabrication release (`--dry-run` supported) |
| `ohmai golden` / `ohmai known-bad` | Run golden reference projects and the planted-bug mutation fixtures (`tests/fixtures/known-bad/`) |

---

## Repo Layout

```text
skill/            SKILL.md (agent entry point) + phases/01..12.md
template/         Project skeleton copied by `ohmai new` (AGENTS.md, STATUS.md, ohmai.yaml, docs/, parts/, hardware/, bom/)
cli/              Python package (`ohmai`): CLI commands, checks, layout helpers, review & datasheet manifest tools
tests/            known-bad mutation fixtures (one planted bug per check) and golden reference projects
scripts/          canary.py (newest KiCad/libs vs the suites), pylock.py
docs/             ARCHITECTURE.md, REVIEWS.md, ROADMAP.md, TESTING.md
```

---

## Pinned tool versions per release

Each ohmai release ships `pinned-tools.yaml` with the exact external tool versions it was
tested with. `ohmai setup` installs those versions with no questions and verifies
checksums; `ohmai doctor` FAILs on a mismatch and prints the expected version, the found
version, and the fix. Set `OHMAI_ALLOW_UNPINNED=1` (or `toolchain.allow_unpinned: true` in
a project) to downgrade a mismatch to a warning when you are testing a newer tool.

This release pins:

| Tool | Pin |
|---|---|
| Freerouting | 2.5.0 jar (GitHub release URL + sha256); use the jar with JDK 25, not the GraalVM native CLI. `ohmai layout autoroute` always passes `--user_data_path` under `~/.local/share/ohmai/freerouting/<ver>/userdata` so the shared `~/.config/freerouting` is never touched. |
| JDK | 25 (major; any 25.x build). Auto-discovered from `~/.local/share/jdk25`, the ohmai cache, or `/workspace/.toolcache/jdk25`; `FREEROUTING_JAVA` remains an override. |
| KiCad (`kicad-cli`) | 9.0.2 tested; accepted range `9.0.x` |
| easyeda2kicad | 1.0.1 (PyPI, AGPL-3.0, subprocess only) |

A project's `toolchain.lock.yaml` is separate: it records the tools that design was built
with. Before tagging a release, bump the pins and re-run the suite plus the Freerouting
smoke test (see [docs/TESTING.md](docs/TESTING.md)).

## License

Apache-2.0 (see [LICENSE](LICENSE) and [NOTICE](NOTICE)). Copyright 2026 The OhmAI contributors. GPL/AGPL tools (`kicad-cli`/pcbnew, Freerouting, easyeda2kicad, KiBot, SKiDL) are invoked as separate external processes and are never vendored or imported. `ohmai setup` may install easyeda2kicad (AGPL-3.0) and the Freerouting jar (GPL-3.0) as separate programs; downloads from EasyEDA follow EasyEDA's terms. Projects you generate with `ohmai new` are yours.
