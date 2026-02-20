"""Shared fixtures — module-scoped DB template eliminates per-test init_db overhead."""

import shutil

import pytest

from indiseek.storage.sqlite_store import SqliteStore
from indiseek.tools.search_code import CodeSearcher

_ALL_TABLES = [
    "symbols", "chunks", "scip_symbols", "scip_occurrences",
    "scip_relationships", "file_summaries", "queries",
    "file_contents", "directory_summaries", "repos", "metadata",
]


@pytest.fixture(scope="module")
def _module_db_path(tmp_path_factory):
    """Create one fully-initialized DB per test module as a template."""
    db_path = tmp_path_factory.mktemp("db") / "test.db"
    s = SqliteStore(db_path)
    s.init_db()
    s._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    s._conn.close()
    return db_path


@pytest.fixture
def db_path(tmp_path, _module_db_path):
    """Copy the template DB into a per-test tmp dir (fast file copy, no init_db)."""
    path = tmp_path / "test.db"
    shutil.copy2(_module_db_path, path)
    return path


@pytest.fixture
def store(db_path):
    """Per-test SqliteStore backed by a pre-initialized DB copy."""
    return SqliteStore(db_path)


@pytest.fixture
def repo_dir(tmp_path):
    """Empty repo directory (files served from SQLite, not disk)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def searcher():
    """CodeSearcher with no backends (for mocked tests)."""
    return CodeSearcher()
