"""Tests for dashboard API repo management and repo-scoped endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from indiseek.storage.sqlite_store import Chunk, SqliteStore


def _make_store_factory(db_path):
    """Create a factory that returns a fresh store connection per call (thread-safe)."""
    def factory():
        return SqliteStore(db_path)
    return factory


@pytest.fixture
def client(store, db_path, tmp_path):
    """Create a TestClient with patched config.

    Uses _get_sqlite_store that creates a new connection each time (thread-safe).
    """
    store_factory = _make_store_factory(db_path)

    with patch("indiseek.api.dashboard._get_sqlite_store", side_effect=lambda: store_factory()), \
         patch("indiseek.api.dashboard._get_vector_store", return_value=None), \
         patch("indiseek.api.dashboard._get_lexical_indexer", return_value=None), \
         patch("indiseek.api.dashboard.config") as mock_config:
        mock_config.SQLITE_PATH = db_path
        mock_config.GEMINI_API_KEY = ""
        mock_config.REPO_PATH = tmp_path / "repo"
        mock_config.REPOS_DIR = tmp_path / "repos"
        mock_config.DATA_DIR = tmp_path / "data"
        mock_config.get_repo_path.side_effect = lambda rid: tmp_path / "repo" if rid == 1 else tmp_path / f"repos/{rid}"
        mock_config.get_lancedb_table_name.side_effect = lambda rid: "chunks" if rid == 1 else f"chunks_{rid}"
        mock_config.get_tantivy_path.side_effect = lambda rid: tmp_path / "tantivy" if rid == 1 else tmp_path / f"tantivy_{rid}"
        mock_config.LANCEDB_PATH = tmp_path / "lancedb"
        mock_config.EMBEDDING_DIMS = 768

        from indiseek.api.dashboard import router, _task_manager
        # Reset task manager to avoid cross-test interference
        _task_manager._tasks.clear()
        _task_manager._subscribers.clear()

        app = FastAPI()
        app.include_router(router)
        yield TestClient(app)


class TestReposCRUD:
    """Test repo CRUD endpoints."""

    def test_list_repos_empty(self, client):
        resp = client.get("/repos")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_repo(self, client, store):
        repo_id = store.insert_repo("myrepo", "/tmp/myrepo", url="https://example.com/repo")
        resp = client.get(f"/repos/{repo_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "myrepo"
        assert data["url"] == "https://example.com/repo"

    def test_get_repo_not_found(self, client):
        resp = client.get("/repos/999")
        assert resp.status_code == 404

    def test_list_repos(self, client, store):
        store.insert_repo("alpha", "/a")
        store.insert_repo("beta", "/b")
        resp = client.get("/repos")
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()]
        assert names == ["alpha", "beta"]

    def test_create_repo_triggers_clone(self, client, store, tmp_path):
        resp = client.post("/repos", json={
            "name": "newrepo",
            "url": "https://example.com/newrepo.git",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "clone"
        assert data["status"] == "running"
        assert "task_id" in data

    def test_create_repo_duplicate_name(self, client, store):
        store.insert_repo("myrepo", "/tmp/myrepo")
        resp = client.post("/repos", json={
            "name": "myrepo",
            "url": "https://example.com/repo.git",
        })
        assert resp.status_code == 409

    def test_delete_repo(self, client, store):
        repo_id = store.insert_repo("myrepo", "/tmp/myrepo")
        resp = client.delete(f"/repos/{repo_id}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert store.get_repo(repo_id) is None

    def test_delete_repo_not_found(self, client):
        resp = client.delete("/repos/999")
        assert resp.status_code == 404


class TestRepoScopedEndpoints:
    """Test that existing endpoints accept repo_id parameter."""

    def test_stats_accepts_repo_id(self, client):
        resp = client.get("/stats?repo_id=1")
        assert resp.status_code == 200

    def test_stats_default_repo_id(self, client):
        resp = client.get("/stats")
        assert resp.status_code == 200

    def test_tree_accepts_repo_id(self, client, tmp_path):
        # Create a repo dir for the tree endpoint
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir(exist_ok=True)
        with patch("indiseek.api.dashboard.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="src/main.ts\n", returncode=0
            )
            resp = client.get("/tree?repo_id=1")
        assert resp.status_code == 200

    def test_files_accepts_repo_id(self, client):
        resp = client.get("/files/src/main.ts?repo_id=1")
        assert resp.status_code == 200

    def test_chunks_accepts_repo_id(self, client, store):
        # Insert a chunk
        store.insert_chunks([Chunk(
            id=None, file_path="a.ts", symbol_name="foo",
            chunk_type="function", content="code",
            start_line=1, end_line=5,
        )], repo_id=1)
        chunks = store.get_chunks_by_file("a.ts", repo_id=1)
        chunk_id = chunks[0]["id"]
        resp = client.get(f"/chunks/{chunk_id}?repo_id=1")
        assert resp.status_code == 200

    def test_search_accepts_repo_id(self, client):
        resp = client.get("/search?q=hello&repo_id=1")
        assert resp.status_code == 200

    def test_queries_accepts_repo_id(self, client):
        resp = client.get("/queries?repo_id=1")
        assert resp.status_code == 200


class TestUnscopedEndpointAliases:
    """Verify that endpoints without repo_id default to repo_id=1."""

    def test_stats_without_repo_id(self, client, store):
        """Stats endpoint works without repo_id, defaulting to repo_id=1."""
        store.insert_chunks([Chunk(
            id=None, file_path="a.ts", symbol_name="foo",
            chunk_type="function", content="code",
            start_line=1, end_line=5,
        )], repo_id=1)
        resp = client.get("/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sqlite"]["chunks"] >= 1

    def test_search_without_repo_id(self, client):
        """Search endpoint works without repo_id."""
        resp = client.get("/search?q=hello")
        assert resp.status_code == 200

    def test_queries_without_repo_id(self, client, store):
        """Queries endpoint works without repo_id, defaulting to repo_id=1."""
        store.insert_query("test prompt", repo_id=1)
        resp = client.get("/queries")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1

    def test_files_without_repo_id(self, client):
        """Files endpoint works without repo_id."""
        resp = client.get("/files/src/main.ts")
        assert resp.status_code == 200


class TestFreshnessCheck:
    """Test the freshness check endpoint."""

    def test_check_not_found(self, client):
        resp = client.post("/repos/999/check")
        assert resp.status_code == 404

    def test_check_no_local_path(self, client, store):
        repo_id = store.insert_repo("test", "/nonexistent/path")
        resp = client.post(f"/repos/{repo_id}/check")
        assert resp.status_code == 400


class TestSyncEndpoint:
    """Test the sync endpoint."""

    def test_sync_not_found(self, client):
        resp = client.post("/repos/999/sync")
        assert resp.status_code == 404

    def test_sync_starts_task(self, client, store, tmp_path):
        repo_dir = tmp_path / "syncrepo"
        repo_dir.mkdir()
        repo_id = store.insert_repo("syncrepo", str(repo_dir))
        resp = client.post(f"/repos/{repo_id}/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "sync"
        assert data["status"] == "running"


class TestIndexingOpsRepoId:
    """Test that indexing operation endpoints accept repo_id."""

    def test_treesitter_accepts_repo_id(self, client, tmp_path):
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir(exist_ok=True)
        resp = client.post("/run/treesitter", json={"repo_id": 1})
        assert resp.status_code == 200

    def test_scip_accepts_repo_id(self, client, tmp_path):
        resp = client.post("/run/scip", json={"repo_id": 1})
        assert resp.status_code == 200

    def test_lexical_accepts_repo_id(self, client, tmp_path):
        resp = client.post("/run/lexical", json={"repo_id": 1})
        assert resp.status_code == 200

    def test_summarize_dirs_accepts_repo_id(self, client, tmp_path):
        # Needs GEMINI_API_KEY — returns 400
        resp = client.post("/run/summarize-dirs", json={"repo_id": 1})
        assert resp.status_code == 400

    def test_query_accepts_repo_id(self, client, tmp_path):
        # Needs GEMINI_API_KEY — returns 400
        resp = client.post("/run/query", json={"prompt": "test", "repo_id": 1})
        assert resp.status_code == 400
