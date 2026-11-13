# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
"""Parse the JSON result line from a subprocess's stdout.

Native KiCad/SWIG workers can print noise after their JSON line at teardown,
e.g. "swig/python detected a memory leak of type ...". Take the last line that
parses as a JSON object and ignore any other lines.
"""
from __future__ import annotations

import json


def last_json_object(stdout: str) -> dict:
    for line in reversed((stdout or '').splitlines()):
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError('no JSON object line in output')
