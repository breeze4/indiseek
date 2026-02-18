"""Tests for multi-repo schema, migrations, CRUD, and data isolation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from indiseek.storage.sqlite_store import Chunk, SqliteStore, Symbol


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


class TestRepoDataIsolation:
    """Verify that data is isolated between repos when using explicit repo_id."""

    def test_symbols_isolated_by_repo_id(self, store):
        """Symbols inserted for repo_id=1 are not visible for repo_id=2."""
        store.insert_symbols([
            Symbol(None, "src/a.ts", "foo", "function", 1, 0, 5, 1, None, None),
        ], repo_id=1)
        store.insert_symbols([
            Symbol(None, "src/a.ts", "bar", "function", 10, 0, 15, 1, None, None),
        ], repo_id=2)
        r1 = store.get_symbols_by_file("src/a.ts", repo_id=1)
        r2 = store.get_symbols_by_file("src/a.ts", repo_id=2)
        assert len(r1) == 1
        assert r1[0]["name"] == "foo"
        assert len(r2) == 1
        assert r2[0]["name"] == "bar"

    def test_chunks_isolated_by_repo_id(self, store):
        """Chunks inserted for repo_id=1 are not visible for repo_id=2."""
        store.insert_chunks([
            Chunk(None, "src/a.ts", "fn1", "function", 1, 5, "code1", 4),
        ], repo_id=1)
        store.insert_chunks([
            Chunk(None, "src/a.ts", "fn2", "function", 1, 5, "code2", 4),
        ], repo_id=2)
        c1 = store.get_chunks_by_file("src/a.ts", repo_id=1)
        c2 = store.get_chunks_by_file("src/a.ts", repo_id=2)
        assert len(c1) == 1
        assert c1[0]["symbol_name"] == "fn1"
        assert len(c2) == 1
        assert c2[0]["symbol_name"] == "fn2"

    def test_file_summaries_isolated_by_repo_id(self, store):
        """File summaries are scoped by repo_id."""
        store.insert_file_summaries([
            ("src/a.ts", "Summary for repo 1", "ts", 10),
        ], repo_id=1)
        store.insert_file_summaries([
            ("src/a.ts", "Summary for repo 2", "ts", 10),
        ], repo_id=2)
        s1 = store.get_file_summaries(repo_id=1)
        s2 = store.get_file_summaries(repo_id=2)
        assert len(s1) == 1
        assert s1[0]["summary"] == "Summary for repo 1"
        assert len(s2) == 1
        assert s2[0]["summary"] == "Summary for repo 2"

    def test_file_contents_isolated_by_repo_id(self, store):
        """File contents are scoped by repo_id."""
        store.insert_file_content("src/a.ts", "content for repo 1", repo_id=1)
        store.insert_file_content("src/a.ts", "content for repo 2", repo_id=2)
        assert store.get_file_content("src/a.ts", repo_id=1) == "content for repo 1"
        assert store.get_file_content("src/a.ts", repo_id=2) == "content for repo 2"

    def test_directory_summaries_isolated_by_repo_id(self, store):
        """Directory summaries are scoped by repo_id."""
        store.insert_directory_summary("src", "Dir summary repo 1", repo_id=1)
        store.insert_directory_summary("src", "Dir summary repo 2", repo_id=2)
        s1 = store.get_directory_summary("src", repo_id=1)
        s2 = store.get_directory_summary("src", repo_id=2)
        assert s1["summary"] == "Dir summary repo 1"
        assert s2["summary"] == "Dir summary repo 2"

    def test_queries_isolated_by_repo_id(self, store):
        """Queries are scoped by repo_id."""
        store.insert_query("prompt for repo 1", repo_id=1)
        store.insert_query("prompt for repo 2", repo_id=2)
        q1 = store.list_queries(repo_id=1)
        q2 = store.list_queries(repo_id=2)
        assert len(q1) == 1
        assert q1[0]["prompt"] == "prompt for repo 1"
        assert len(q2) == 1
        assert q2[0]["prompt"] == "prompt for repo 2"

    def test_clear_index_data_scoped_by_repo_id(self, store):
        """clear_index_data only affects the specified repo_id."""
        store.insert_chunks([
            Chunk(None, "a.ts", "fn1", "function", 1, 5, "code1", 4),
        ], repo_id=1)
        store.insert_chunks([
            Chunk(None, "a.ts", "fn2", "function", 1, 5, "code2", 4),
        ], repo_id=2)
        store.clear_index_data(repo_id=1)
        # repo_id=1 data should be gone
        assert store.get_chunks_by_file("a.ts", repo_id=1) == []
        # repo_id=2 data should remain
        c2 = store.get_chunks_by_file("a.ts", repo_id=2)
        assert len(c2) == 1

    def test_scip_symbols_isolated_by_repo_id(self, store):
        """SCIP symbol lookup is scoped by repo_id."""
        sid1 = store.insert_scip_symbol("npm . pkg 1.0 `foo`.", repo_id=1)
        sid2 = store.insert_scip_symbol("npm . pkg 1.0 `foo`.", repo_id=2)
        # Same symbol string, different repos → different IDs
        assert sid1 != sid2
        assert store.get_scip_symbol_id("npm . pkg 1.0 `foo`.", repo_id=1) == sid1
        assert store.get_scip_symbol_id("npm . pkg 1.0 `foo`.", repo_id=2) == sid2

    def test_count_scoped_by_repo_id(self, store):
        """count() filters by repo_id for data tables."""
        store.insert_chunks([
            Chunk(None, "a.ts", "fn1", "function", 1, 5, "code1", 4),
            Chunk(None, "b.ts", "fn2", "function", 1, 5, "code2", 4),
        ], repo_id=1)
        store.insert_chunks([
            Chunk(None, "c.ts", "fn3", "function", 1, 5, "code3", 4),
        ], repo_id=2)
        assert store.count("chunks", repo_id=1) == 2
        assert store.count("chunks", repo_id=2) == 1
