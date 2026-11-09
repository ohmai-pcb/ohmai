# SPDX-License-Identifier: Apache-2.0
"""Git/LFS integration tests must operate only on disposable homes."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_global_git_config_is_actually_test_local(tmp_path):
    path = Path(os.environ['GIT_CONFIG_GLOBAL'])
    assert path.is_relative_to(tmp_path)
    assert Path.home() == tmp_path / 'test-home'
    subprocess.run(['git', 'config', '--global', 'ohmai.isolation-probe', 'scratch-only'], check=True)
    assert 'scratch-only' in path.read_text()
    assert os.environ['GIT_CONFIG_NOSYSTEM'] == '1'
    assert Path.cwd() == tmp_path


@pytest.mark.skipif(not shutil.which('git-lfs'), reason='optional git-lfs missing')
def test_unscoped_lfs_install_is_confined_to_test_home(tmp_path):
    subprocess.run(['git', 'lfs', 'install', '--skip-repo'], check=True, capture_output=True)
    path = Path(os.environ['GIT_CONFIG_GLOBAL'])
    assert path.is_relative_to(tmp_path)
    assert 'filter "lfs"' in path.read_text()
