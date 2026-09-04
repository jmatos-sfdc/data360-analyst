"""Shared pytest fixtures for the data360-analyst tests."""
from pathlib import Path
import shutil

import pytest

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "mini-client"


@pytest.fixture
def mini_client(tmp_path):
    """Copy the mini-client fixture to a tmp dir so tests can write to it
    without polluting the checked-in fixture. Returns the tmp root path."""
    dst = tmp_path / "mini-client"
    shutil.copytree(FIXTURE_ROOT, dst)
    return dst
