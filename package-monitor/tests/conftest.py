"""Shared pytest fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def db_conn(tmp_path):
    from scm.db import init_db

    conn = init_db(tmp_path / "test.db")
    yield conn
    conn.close()


@pytest.fixture
def malicious_diff():
    return (Path(__file__).parent / "fixtures" / "sample_diff_malicious.md").read_text()


@pytest.fixture
def benign_diff():
    return (Path(__file__).parent / "fixtures" / "sample_diff_benign.md").read_text()


@pytest.fixture
def npm_changes():
    return json.loads(
        (Path(__file__).parent / "fixtures" / "npm_changes.json").read_text()
    )
