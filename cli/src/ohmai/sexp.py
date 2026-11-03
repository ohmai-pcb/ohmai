"""Minimal S-expression reader for KiCad files (.kicad_sch, kicadsexpr netlists).

parse(text) -> nested lists; atoms are str (quotes removed). No dependencies.
"""
from __future__ import annotations

import re


def tokenize(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            yield ("S", "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            yield ("A", text[i:j])
            i = j


def parse(text: str):
    stack: list[list] = [[]]
    for tok in tokenize(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            if len(stack) < 2:
                raise ValueError("unbalanced ')'")
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(tok[1])
    if len(stack) != 1:
        raise ValueError("unbalanced '('")
    return stack[0][0] if len(stack[0]) == 1 else stack[0]


def find(node, key):
    """Direct children lists whose head is key."""
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def first(node, key):
    r = find(node, key)
    return r[0] if r else None


def walk(node, key):
    """All descendant lists whose head is key."""
    if isinstance(node, list):
        if node and node[0] == key:
            yield node
        for c in node:
            if isinstance(c, list):
                yield from walk(c, key)


_BARE_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$|^[A-Za-z_][A-Za-z0-9_./+-]*$")


def _fmt_atom(a: str, is_head: bool = False) -> str:
    s = str(a)
    if is_head:
        return s
    if s == "":
        return '""'
    if ":" in s or " " in s or "(" in s or ")" in s or "\"" in s or not _BARE_RE.match(s):
        esc = s.replace("\\", "\\\\").replace("\"", "\\\"")
        return f'"{esc}"'
    return s


def dumps(node, indent: int = 0) -> str:
    """Serialize nested S-expression lists back to KiCad S-expression text."""
    if not isinstance(node, list):
        return _fmt_atom(node)
    if not node:
        return "()"
    head = _fmt_atom(node[0], is_head=True)
    has_sublist = any(isinstance(c, list) for c in node[1:])
    if not has_sublist:
        atoms = " ".join(_fmt_atom(c) for c in node[1:])
        return f"({head} {atoms})" if atoms else f"({head})"
    cpad = "  " * (indent + 1)
    parts = [f"({head}"]
    inline_prefix = []
    idx = 1
    while idx < len(node) and not isinstance(node[idx], list):
        inline_prefix.append(_fmt_atom(node[idx]))
        idx += 1
    if inline_prefix:
        parts[0] += " " + " ".join(inline_prefix)
    for c in node[idx:]:
        if isinstance(c, list):
            parts.append(f"\n{cpad}{dumps(c, indent + 1)}")
        else:
            parts.append(f" {_fmt_atom(c)}")
    parts.append(")")
    return "".join(parts)
