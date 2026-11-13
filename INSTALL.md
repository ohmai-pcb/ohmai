<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
# Install OhmAI (0.2.0 release preparation)

The agent plugin is **guidance only**. It bundles no CLI, third-party Python code,
KiCad/pcbnew, Freerouting, EasyEDA/LCSC CAD content or converter. Install the CLI
and toolchain separately **on the computer that executes checks**. Never label
ERC/DRC enabled merely because a skill was copied.

**Not published yet:** the prepared version is `0.2.0` / future tag `v0.2.0` (created at release).
Do not use the pinned remote commands until the owner publishes that tag. Existing
`v0.1.0` does not contain this plugin. No marketplace submission is implied.

## 1. CLI and KiCad (all hosts)

```sh
python3 -m venv "$HOME/.local/share/ohmai/venv"
. "$HOME/.local/share/ohmai/venv/bin/activate"
# After the release tag exists:
python -m pip install 'git+https://github.com/ohmai-pcb/ohmai.git@v0.2.0#subdirectory=cli'
# Before publication, from a trusted source checkout instead:
# python -m pip install ./cli

ohmai --version
ohmai setup                      # pin-for-pin install: Freerouting jar, easyeda2kicad, check KiCad/JDK
kicad-cli version                 # this release: 9.0.2 (accepted 9.0.x)
# In an existing project: also respect toolchain.lock exact pins.
ohmai doctor                     # missing/wrong pinned tool is FAIL (exit 1)
```

Use an isolated venv; do not replace system Python. Git must be installed for the
VCS pip URL. On Grok Bot prefer agent self-install on its **own cloud computer**,
not a request for the owner to install locally. Debian/Ubuntu: inspect
`apt-cache policy kicad`; install a verified 9.x package, not whichever major is
current. A different OS uses the official KiCad 9 installer. Version locks may
require exactly 9.0.2. Use `KICAD_CLI` for a non-default executable. Permission or
network blockers must be reported; never skip verification and call it PASS.

## 2. Build a clean local plugin/marketplace

From the trusted source checkout (CLI dependencies provide YAML/jsonschema):

```sh
python scripts/package_plugin.py --output dist/ohmai-marketplace
python scripts/validate_plugin.py --tag v0.2.0
python scripts/validate_plugin.py --marketplace dist/ohmai-marketplace
```

The output directory must be **new**; the builder refuses overwrites and symlinks.
Use a new versioned output name for another build. The output plugin is
`dist/ohmai-marketplace/plugins/ohmai`. Source-tree marketplace files are build
**templates**: do not register the checkout root as a marketplace. Distribute only
the built marketplace or plugin, never the whole tool repository. Root manifests
are build inputs; installing the repository wholesale bypasses the payload audit.

## Cursor

Portable skill install (no marketplace or paid cloud agent required):

```sh
ohmai skill install                 # ~/.cursor/skills/ohmai
# Project scope instead: ohmai skill install --dest .cursor/skills/ohmai
```

Restart/reload Cursor, open Customize → Skills and check OhmAI; invoke `/ohmai`.
For a full local plugin, copy the **built plugin**, not the repository, into a new
`~/.cursor/plugins/local/ohmai` directory; review/trust it and reload. A published
marketplace artifact can be imported through Customize; do not submit/publish it
without owner approval. A local file smoke test does not prove UI discovery.

## Grok Build

Review the built guidance before granting trust. With the Grok CLI installed:

```sh
grok plugin validate ./dist/ohmai-marketplace/plugins/ohmai
grok plugin install ./dist/ohmai-marketplace/plugins/ohmai --trust
grok plugin enable ohmai
grok plugin list
# Alternative: register the built local catalog, then use its plugin entry.
grok plugin marketplace add ./dist/ohmai-marketplace
```

Installation requires explicit trust and normally enables the plugin; `enable`
is shown to make the desired state clear. Start a fresh session and request an
OhmAI idea-to-PCB workflow. No MCP servers or runtime hooks are installed.

## Grok Bot

Do not confuse Grok Build's CLI plugin loader with Grok Bot's skill catalog.
Until a native Bot catalog import is available/verified, use this explicit,
file-backed bootstrap on the bot's execution computer:

```sh
ohmai skill install --dest "$HOME/.local/share/ohmai/grok-bot/skills/ohmai"
ohmai --version
ohmai doctor
```

Then tell the bot: “Read `~/.local/share/ohmai/grok-bot/skills/ohmai/SKILL.md` and
follow it for this PCB project; read its phase guides when needed.” This path is
ordinary app data, **not a claim of automatic catalog discovery**. For persistent
activation use the Bot's supported skill-import interface if offered, importing
the complete canonical skill folder; never write into managed skill stores by
hand. Native Bot import/trust UX still needs an owner-host smoke test.

## Claude Code

```sh
claude plugin validate --strict ./dist/ohmai-marketplace/plugins/ohmai
claude --plugin-dir ./dist/ohmai-marketplace/plugins/ohmai   # session-only trial
# Persistent local marketplace install:
claude plugin marketplace add ./dist/ohmai-marketplace
claude plugin install ohmai@ohmai-local
claude plugin enable ohmai@ohmai-local
```

Review/trust the local source when prompted, then start/reload a session and
invoke `/ohmai:ohmai`. Choose user/project/local scope intentionally; do not commit
another user's settings. The plugin adds no credentials, MCP servers or hooks.

## Upgrade, disable, uninstall

- Skill copy: `ohmai skill install --force` (or the same `--dest`) backs up the
  previous directory as `ohmai.bak.<timestamp>...`; review then remove only that
  install directory to uninstall. Do not delete project `.ohmai/phases`.
- Grok Build: `grok plugin disable ohmai`; remove with `grok plugin uninstall ohmai`.
  Remove the catalog with `grok plugin marketplace remove ohmai-local` if registered.
- Claude Code: `claude plugin disable ohmai@ohmai-local`, then
  `claude plugin uninstall ohmai@ohmai-local`; remove the local marketplace if no
  longer needed (`claude plugin marketplace remove ohmai-local`). Session-only
  `--plugin-dir` use stops when you omit the flag.
- Cursor: disable/uninstall through Customize, or remove the local plugin/skill
  directory you created, then reload.
- Grok Bot file-backed mode: remove only the explicit copied skill directory;
  native catalog installs must be removed through the supported host interface.
- CLI: `python -m pip uninstall ohmai` in its venv. Uninstalling the plugin does
  **not** uninstall KiCad/Freerouting or delete user projects. Remove external
  tools separately only with the owner's agreement.

## Pinned tool versions per release

`pinned-tools.yaml` (shipped with the CLI package) is the source of truth for the
external tools this ohmai release was tested with. `ohmai setup` fetches those
exact versions with no prompts and verifies checksums (Freerouting jar sha256,
easyeda2kicad PyPI hashes); it checks KiCad and JDK 25 in place because those
are system installs. `ohmai doctor` FAILs when a tool's version differs from the
pin, and prints the expected version, the found version, and the fix (`ohmai setup`,
or `OHMAI_ALLOW_UNPINNED=1` to test a different version).

This is separate from a project's `toolchain.lock.yaml`, which records the tools
that particular design was built with.

## Release validation

See [packaging and validation](https://github.com/ohmai-pcb/ohmai/blob/main/docs/PLUGIN-PACKAGING.md) in the source repository.
Native host validation is required before advertising host compatibility. A local
contract-schema pass cannot substitute for host installation/discovery tests.
NOTICE is resolved (see [NOTICE](NOTICE)). The full git history will be published
as is; do not rewrite it. Publish the matching semver release only after approval,
and pin any future remote marketplace entry to its full release commit SHA. This
package does not auto-submit issues or marketplace entries.
