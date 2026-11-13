# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 The OhmAI contributors
import pytest

from ohmai.jsonout import last_json_object

LEAK = "swig/python detected a memory leak of type 'std::shared_ptr< SHAPE > *', no destructor found."


def test_trailing_swig_leak_line_ignored():
    assert last_json_object('{"ok": true, "conflicts": []}\n' + LEAK + '\n') == {'ok': True, 'conflicts': []}


def test_plain_and_leading_noise():
    assert last_json_object('loading\n{"ok": false}\n') == {'ok': False}


def test_last_object_wins():
    assert last_json_object('{"a": 1}\n{"a": 2}\n' + LEAK) == {'a': 2}


@pytest.mark.parametrize('out', ['', LEAK, '[1, 2]', '{broken'])
def test_no_object_raises(out):
    with pytest.raises(ValueError):
        last_json_object(out)
