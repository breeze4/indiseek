"""Tests for multi-repo schema, migrations, and CRUD methods."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from indiseek.storage.sqlite_store import SqliteStore, Symbol


@pytest.fixture
def store(tmp_path):
    """Create a fresh SqliteStore with schema initialized."""
    db = SqliteStore(tmp_path / "test.db")
    db.init_db()
    return db


class TestReposTable:
    """Verify the repos table is created with correct schema."""

    def test_repos_table_exists(self, store):
        cur = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='repos'"
        )
        assert cur.fetchone() is not None

    def test_repos_table_columns(self, store):
        cur = store._conn.execute("PRAGMA table_info(repos)")
        columns = {row[1] for row in cur.fetchall()}
        expected = {
            "id", "name", "url", "local_path", "created_at",
            "last_indexed_at", "indexed_commit_sha", "current_commit_sha",
            "commits_behind", "status",
        }
        assert columns == expected

    def test_repos_name_index_exists(self, store):
        cur = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_repos_name'"
        )
        assert cur.fetchone() is not None


class TestRepoIdMigrations:
    """Verify repo_id column added to all data tables."""

    @pytest.mark.parametrize("table", [
        "symbols", "chunks", "file_summaries",
        "scip_symbols", "scip_occurrences", "scip_relationships",
        "queries", "file_contents", "directory_summaries",
    ])
    def test_repo_id_column_exists(self, store, table):
        cur = store._conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1]: row[4] for row in cur.fetchall()}  # name -> default
        assert "repo_id" in columns

    @pytest.mark.parametrize("table", [
        "symbols", "chunks", "file_summaries",
        "scip_symbols", "scip_occurrences", "scip_relationships",
        "queries", "file_contents", "directory_summaries",
    ])
    def test_repo_id_default_is_1(self, store, table):
        cur = store._conn.execute(f"PRAGMA table_info({table})")
        for row in cur.fetchall():
            if row[1] == "repo_id":
                assert row[4] == "1", f"{table}.repo_id default should be 1"
                break

    @pytest.mark.parametrize("table", [
        "symbols", "chunks", "file_summaries",
        "scip_symbols", "scip_occurrences", "scip_relationships",
        "queries", "file_contents", "directory_summaries",
    ])
    def test_repo_id_index_exists(self, store, table):
        idx_name = f"idx_{table}_repo_id"
        cur = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            (idx_name,),
        )
        assert cur.fetchone() is not None, f"Index {idx_name} should exist"

    def test_migration_is_idempotent(self, tmp_path):
        """Calling init_db() twice doesn't fail."""
        db = SqliteStore(tmp_path / "test.db")
        db.init_db()
        db.init_db()  # should not raise
        # Verify repo_id still has correct default
        cur = db._conn.execute("PRAGMA table_info(symbols)")
        for row in cur.fetchall():
            if row[1] == "repo_id":
                assert row[4] == "1"
                break


class TestLegacyRepoAutoCreation:
    """Verify legacy repo is auto-created when data exists."""

    def test_no_legacy_repo_when_no_data(self, store):
        """No legacy repo if no indexed data."""
        repos = store.list_repos()
        assert len(repos) == 0

    def test_legacy_repo_created_when_symbols_exist(self, tmp_path):
        """Legacy repo created when symbols exist but repos is empty."""
        db = SqliteStore(tmp_path / "test.db")
        db.init_db()
        # Insert a symbol to simulate existing data
        db.insert_symbols([Symbol(
            id=None, file_path="src/foo.ts", name="foo",
            kind="function", start_line=1, start_col=0,
            end_line=5, end_col=1,
        )])
        # Re-init to trigger legacy repo creation
        db.init_db()
        repos = db.list_repos()
        assert len(repos) == 1
        assert repos[0]["id"] == 1
        assert repos[0]["status"] == "active"

    def test_legacy_repo_uses_repo_path_env(self, tmp_path):
        """Legacy repo name derived from REPO_PATH env var."""
        db = SqliteStore(tmp_path / "test.db")
        db.init_db()
        db.insert_symbols([Symbol(
            id=None, file_path="src/foo.ts", name="foo",
            kind="function", start_line=1, start_col=0,
            end_line=5, end_col=1,
        )])
        with patch.dict("os.environ", {"REPO_PATH": "/home/user/repos/vite"}):
            db.init_db()
        repos = db.list_repos()
        assert repos[0]["name"] == "vite"
        assert repos[0]["local_path"] == "/home/user/repos/vite"

    def test_legacy_repo_fallback_name(self, tmp_path):
        """Legacy repo uses 'legacy' name when REPO_PATH is empty."""
        db = SqliteStore(tmp_path / "test.db")
        db.init_db()
        db.insert_symbols([Symbol(
            id=None, file_path="src/foo.ts", name="foo",
            kind="function", start_line=1, start_col=0,
            end_line=5, end_col=1,
        )])
        with patch.dict("os.environ", {"REPO_PATH": ""}, clear=False):
            db.init_db()
        repos = db.list_repos()
        assert repos[0]["name"] == "legacy"
        assert repos[0]["local_path"] == "."

    def test_legacy_repo_not_recreated(self, tmp_path):
        """Legacy repo not recreated if repos already has data."""
        db = SqliteStore(tmp_path / "test.db")
        db.init_db()
        db.insert_symbols([Symbol(
            id=None, file_path="src/foo.ts", name="foo",
            kind="function", start_line=1, start_col=0,
            end_line=5, end_col=1,
        )])
        with patch.dict("os.environ", {"REPO_PATH": "/repos/vite"}):
            db.init_db()
        # Re-init again — should not create another row
        db.init_db()
        repos = db.list_repos()
        assert len(repos) == 1


class TestRepoCRUD:
    """Test repo insert/get/list/update/delete methods."""

    def test_insert_repo(self, store):
        repo_id = store.insert_repo("myrepo", "/path/to/repo")
        assert repo_id is not None
        assert repo_id > 0

    def test_get_repo(self, store):
        repo_id = store.insert_repo("myrepo", "/path/to/repo", url="https://github.com/org/myrepo")
        repo = store.get_repo(repo_id)
        assert repo is not None
        assert repo["name"] == "myrepo"
        assert repo["local_path"] == "/path/to/repo"
        assert repo["url"] == "https://github.com/org/myrepo"
        assert repo["status"] == "active"
        assert repo["commits_behind"] == 0
        assert repo["created_at"] is not None

    def test_get_repo_not_found(self, store):
        assert store.get_repo(999) is None

    def test_get_repo_by_name(self, store):
        store.insert_repo("myrepo", "/path/to/repo")
        repo = store.get_repo_by_name("myrepo")
        assert repo is not None
        assert repo["name"] == "myrepo"

    def test_get_repo_by_name_not_found(self, store):
        assert store.get_repo_by_name("nonexistent") is None

    def test_list_repos_empty(self, store):
        assert store.list_repos() == []

    def test_list_repos_ordered_by_name(self, store):
        store.insert_repo("zebra", "/z")
        store.insert_repo("alpha", "/a")
        store.insert_repo("middle", "/m")
        repos = store.list_repos()
        names = [r["name"] for r in repos]
        assert names == ["alpha", "middle", "zebra"]

    def test_update_repo(self, store):
        repo_id = store.insert_repo("myrepo", "/path/to/repo")
        store.update_repo(repo_id, status="indexing", indexed_commit_sha="abc123")
        repo = store.get_repo(repo_id)
        assert repo["status"] == "indexing"
        assert repo["indexed_commit_sha"] == "abc123"
        # Unchanged fields preserved
        assert repo["name"] == "myrepo"
        assert repo["local_path"] == "/path/to/repo"

    def test_update_repo_no_kwargs(self, store):
        """update_repo with no kwargs is a no-op."""
        repo_id = store.insert_repo("myrepo", "/path/to/repo")
        store.update_repo(repo_id)  # should not raise
        repo = store.get_repo(repo_id)
        assert repo["name"] == "myrepo"

    def test_delete_repo(self, store):
        repo_id = store.insert_repo("myrepo", "/path/to/repo")
        store.delete_repo(repo_id)
        assert store.get_repo(repo_id) is None

    def test_delete_repo_nonexistent(self, store):
        """Deleting a nonexistent repo doesn't raise."""
        store.delete_repo(999)  # should not raise

    def test_insert_repo_unique_name(self, store):
        """Inserting a repo with a duplicate name raises."""
        store.insert_repo("myrepo", "/path1")
        with pytest.raises(Exception):  # sqlite3.IntegrityError
            store.insert_repo("myrepo", "/path2")
