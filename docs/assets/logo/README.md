<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- Copyright 2026 The OhmAI contributors -->
# OhmAI logo and icons

Concept D (locked). The wordmark and icons are original artwork. The “hmAI”
letterforms are outlined paths derived from [Inter](https://rsms.me/inter/)
(SIL Open Font License 1.1); the Inter font files themselves are not shipped
here. See the root [NOTICE](../../../NOTICE).

## Brand colours

| Role | Hex |
|---|---|
| Navy background | `#0B1F3A` |
| Copper traces and “AI” | `#B87333` |
| White omega and “hm” | `#FFFFFF` |

## Wordmark

Use the transparent SVGs on pages and in the README. PNGs (1× and 2×) are
provided for places that cannot take an SVG.

| File | Use |
|---|---|
| `ohmai-logo-dark-transparent.svg` | Dark or navy backgrounds; default README wordmark |
| `ohmai-logo-light-transparent.svg` | Light backgrounds (`prefers-color-scheme: light`) |
| `ohmai-logo-dark.svg` / `.png` | Same art on an opaque navy field |
| `ohmai-logo-light.svg` / `.png` | Same art on an opaque white field |

## Icons and favicon

Square and circle app icons, with a navy field:

- SVG masters: `ohmai-icon-square.svg`, `ohmai-icon-circle.svg`
- PNG sizes: 512, 256, 128, 64, 32 (`ohmai-icon-{square,circle}-N.png`), plus
  the original 512 and `@2x` (1024) renders
- `favicon.ico`: 16, 32, 48 and 64 px, from the square icon

The 32 px icon keeps a readable Ω, but the copper pads and zigzag resistor
collapse and are not legible as a circuit. Prefer 64 px or larger when the
full mark must be visible.

## KiCad PCM icon

KiCad’s plugin manager expects a 64×64 `resources/icon.png` in a PCM package.
This repository does not yet ship a KiCad PCM plugin. The ready file is
`pcm/resources/icon.png` (square, 64×64). Copy it into `resources/icon.png`
of a PCM artifact when that packaging is added, and include it in the build.
