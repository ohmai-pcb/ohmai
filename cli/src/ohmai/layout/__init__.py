# SPDX-License-Identifier: Apache-2.0
"""Generic layout steps (phase 8): placement model, escape-room rules, free-area scan, grid router core, pour and
DSN helpers, and the KiCad-side runner.

Licensing boundary: nothing in this package imports KiCad's pcbnew (GPL). The pcbnew side lives in
`native_guard.py (isolated _REFILL subprocess script)`, which runs ONLY as a separate process in KiCad's Python (`ohmai layout ...` starts it). The
modules here are plain Python (placement, config) or need the optional `layout` extra (numpy, shapely; BSD).
Freerouting (GPL) is only ever started as a separate Java process.
"""
