# SPDX-License-Identifier: Apache-2.0
"""Hermetic Git/LFS homes: tests must not alter the runner's accounts or repos."""

import pytest


@pytest.fixture(autouse=True)
def isolated_git_home(tmp_path, monkeypatch):
    home = tmp_path / 'test-home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(home / '.gitconfig'))
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', str(home / 'system.gitconfig'))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    monkeypatch.setenv('GIT_ATTR_NOSYSTEM', '1')
    for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR'):
        monkeypatch.delenv(key, raising=False)
    # Accidental unscoped git/lfs calls must run in scratch, never the real repo.
    monkeypatch.chdir(tmp_path)
