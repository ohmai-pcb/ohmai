<!-- SPDX-License-Identifier: Apache-2.0 -->
# Agent plugin packaging and release checks

## One source of truth

`skills/ohmai/` is canonical. `skill` is a compatibility symlink, not a second
maintained tree. `ohmai skill install` and the wheel's `_data/skill` both use the
canonical content. The Hatch hook embeds resources at sdist build time and uses
those same resources for a wheel built from the sdist. Generated projects record
the CLI/skill version through the existing `{{ohmai_version}}` placeholder.

`plugin.json`, `.grok-plugin/plugin.json`, `.claude-plugin/plugin.json` and
`.cursor-plugin/plugin.json` must match. Their version, CLI version, skill metadata
and intended `vX.Y.Z` release tag must agree; marketplace entries omit duplicated
plugin versions. `scripts/validate_plugin.py --release` requires an exact HEAD tag
and a resolved NOTICE. It never creates tags, publishes or rewrites history.

The marketplace files in the source tree are **build templates**, not a usable
source-root marketplace. Build an allowlisted artifact with
`scripts/package_plugin.py --output <NEW-directory>`; it contains an ordinary
`plugins/ohmai/` and local catalog entries. Do not install or ship the whole
repository as a plugin: it includes CLI source and synthetic CAD test fixtures.

Only original Markdown skill/procedure guidance, manifests, INSTALL, LICENSE and
NOTICE are copied. No tools, dependencies, boards, footprints, models, CAD libraries
or supplier data are included. GPL/AGPL programs are separate optional installs.
`easyeda2kicad` is **AGPL-3.0**, subprocess only, never a plugin dependency.

## Documented contracts (checked 2026-11-09)

Our `docs/plugin-schemas/*.schema.json` are a **strict subset for the fields we
use**, authored from these references; they are not official upstream schemas and
cannot establish host runtime compatibility:

- Grok marketplace [README](https://github.com/xai-org/plugin-marketplace/blob/main/README.md)
  and [catalog validator](https://github.com/xai-org/plugin-marketplace/blob/main/scripts/validate-catalog.py).
  Remote URL entries require full 40-character commit SHA pins; our local sources
  do not fetch anything and need no invented SHA.
- Grok Build plugin guide [public mirror](https://grok-wiki.com/public/docs/xai-org-grok-build-90205de50458/pages/12-plugins.md)
  (source: `xai-org/grok-build`, user guide `09-plugins.md`). Root/Grok/Claude manifest
  lookup, metadata, relative skill paths and trust/enable lifecycle are documented.
- [Claude manifest](https://code.claude.com/docs/en/plugins/manifest-reference),
  [marketplace](https://code.claude.com/docs/en/plugins/marketplace-reference)
  and [plugin overview](https://code.claude.com/docs/en/plugins).
- [Cursor plugin reference](https://cursor.com/docs/reference/plugins) and
  [skill discovery](https://cursor.com/docs/context/skills).

The shared metadata shape deliberately avoids hooks, MCP, credentials and host
macros. Validation checks types, kebab names, semver, license, metadata consistency,
frontmatter triggers, real relative catalog destinations in built artifacts, and
no actual GPL imports in the Apache process. It does not emulate host activation,
permissions, billing, marketplace fetches, or OS-specific KiCad installation.

## Maintainer commands

```sh
python scripts/validate_plugin.py --tag v0.1.1
python scripts/package_plugin.py --output dist/ohmai-marketplace
python scripts/validate_plugin.py --marketplace dist/ohmai-marketplace
grok plugin validate dist/ohmai-marketplace/plugins/ohmai
claude plugin validate --strict dist/ohmai-marketplace/plugins/ohmai
claude plugin validate --strict dist/ohmai-marketplace/.claude-plugin/marketplace.json
# Once copyright/release approval and the matching local tag exist:
python scripts/validate_plugin.py --release
```

Run host validators only when installed; otherwise record **missing**, run the
contract checks, and keep native-host validation as an explicit release blocker.
Use a temporary HOME and GIT_CONFIG_GLOBAL for all install smokes; never change a
real profile or global Git/LFS configuration. Verify copied skill content, wheel
resources, `ohmai --version`, successful KiCad 9.x doctor, and missing/wrong-KiCad
FAIL. Keep the separate SPDX notices and licensing boundaries intact.

## Publication gate

- Bump `pinned-tools.yaml` and re-run the suite plus the Freerouting smoke test before tagging
  (see [TESTING.md](TESTING.md#release-checklist-tool-pins)).
- NOTICE names the copyright holder as The OhmAI contributors.
- The full git history will be published as is; do not rewrite it. Current-tree
  cleanup does not remove historical blobs.
- Run native host installs and discovery, including Grok Bot's supported import UX.
- Choose/publish the matching semver tag and built guidance-only artifact after approval.
- Fill the mandatory project [tool issue report](templates/tool-issues.md).
- There is no KiCad PCM package in this repository yet. A 64×64 PCM-ready
  icon lives at [docs/assets/logo/pcm/resources/icon.png](assets/logo/pcm/resources/icon.png);
  copy it to `resources/icon.png` in a PCM artifact when that packaging is added.
