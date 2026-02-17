"""Comprehensive tests for the query caching system.

Covers:
- compute_query_similarity() normalization, thresholds, edge cases
- QueryCache in-memory fuzzy deduplication
- SqliteStore persistent query lifecycle and filtering
- Dashboard /run/query endpoint cache integration
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from indiseek.storage.sqlite_store import SqliteStore
from indiseek.tools.search_code import QueryCache, compute_query_similarity


# ── Fixtures ──


@pytest.fixture
def store(tmp_path):
    """Create a fresh SqliteStore with schema initialized."""
    db = SqliteStore(tmp_path / "test.db")
    db.init_db()
    return db


# ── compute_query_similarity tests ──


class TestComputeQuerySimilarity:
    """Tests for Jaccard similarity on normalized token sets."""

    # -- Normalization --

    def test_identical(self):
        assert compute_query_similarity("hello world", "hello world") == 1.0

    def test_case_insensitive(self):
        assert compute_query_similarity("Hello World", "hello world") == 1.0

    def test_punctuation_stripped(self):
        assert compute_query_similarity("How does X work?", "How does X work") == 1.0

    def test_same_tokens_different_order(self):
        assert compute_query_similarity("foo bar baz", "baz foo bar") == 1.0

    def test_whitespace_variations(self):
        assert compute_query_similarity("  hello   world  ", "hello world") == 1.0

    def test_underscore_preserved(self):
        assert compute_query_similarity("my_func works", "my_func works") == 1.0

    # -- Threshold boundaries --

    def test_at_threshold_four_of_five(self):
        # 4 shared tokens + 1 extra = Jaccard 4/5 = 0.8
        sim = compute_query_similarity("a b c d", "a b c d e")
        assert sim == pytest.approx(0.8)

    def test_below_threshold_four_of_six(self):
        # 4 shared tokens + 2 extra = Jaccard 4/6 ≈ 0.667
        sim = compute_query_similarity("a b c d", "a b c d e f")
        assert sim == pytest.approx(4 / 6)
        assert sim < 0.8

    def test_completely_different(self):
        assert compute_query_similarity("alpha beta", "gamma delta") == 0.0

    # -- Edge cases --

    def test_empty_both(self):
        assert compute_query_similarity("", "") == 0.0

    def test_empty_one(self):
        assert compute_query_similarity("", "hello") == 0.0
        assert compute_query_similarity("hello", "") == 0.0

    def test_single_token_exact(self):
        assert compute_query_similarity("hello", "hello") == 1.0

    def test_single_token_case(self):
        assert compute_query_similarity("Hello", "hello") == 1.0

    def test_single_token_mismatch(self):
        assert compute_query_similarity("hello", "world") == 0.0

    def test_punctuation_only(self):
        # Punctuation-only strings normalize to empty token set
        assert compute_query_similarity("???", "!!!") == 0.0


# ── QueryCache in-memory tests ──


class TestQueryCacheInMemory:
    """Tests for the in-memory fuzzy query cache used within agent runs."""

    def test_empty_returns_none(self):
        cache = QueryCache()
        assert cache.get("anything") is None

    def test_exact_match(self):
        cache = QueryCache()
        cache.put("hello world", "result-1")
        assert cache.get("hello world") == "result-1"

    def test_fuzzy_above_threshold(self):
        cache = QueryCache()
        cache.put("HMR CSS hot update", "result-2")
        # Same tokens, different order — Jaccard = 1.0
        assert cache.get("CSS HMR hot update") == "result-2"

    def test_fuzzy_below_threshold_returns_none(self):
        cache = QueryCache()
        cache.put("HMR CSS propagation", "result-3")
        assert cache.get("createServer module graph") is None

    def test_returns_first_hit_not_best(self):
        cache = QueryCache()
        # Entry A: shares 4/5 tokens with query = 0.8 (at threshold)
        cache.put("a b c d extra", "result-A")
        # Entry B: exact match with query = 1.0
        cache.put("a b c d", "result-B")
        # QueryCache.get() returns first match above threshold, which is A
        assert cache.get("a b c d") == "result-A"

    def test_clear(self):
        cache = QueryCache()
        cache.put("hello", "result")
        cache.clear()
        assert cache.get("hello") is None

    def test_custom_threshold(self):
        # 4 shared + 2 extra = 4/6 ≈ 0.667, fails at 0.8 but passes at 0.5
        cache = QueryCache(threshold=0.5)
        cache.put("a b c d e f", "result-low")
        assert cache.get("a b c d") == "result-low"

    def test_custom_threshold_high_rejects(self):
        # Same setup but with threshold=0.9
        cache = QueryCache(threshold=0.9)
        cache.put("a b c d extra", "result-high")
        # 4/5 = 0.8 < 0.9 threshold
        assert cache.get("a b c d") is None


# ── SqliteStore query lifecycle tests ──


class TestSqliteQueryLifecycle:
    """Tests for insert, complete, fail, cached query operations."""

    def test_insert_creates_running(self, store):
        qid = store.insert_query("test prompt")
        row = store.get_query(qid)
        assert row is not None
        assert row["status"] == "running"
        assert row["answer"] is None
        assert row["prompt"] == "test prompt"

    def test_complete_sets_answer(self, store):
        qid = store.insert_query("test prompt")
        store.complete_query(qid, "the answer", '[{"tool":"t","args":{},"summary":"s"}]', 2.5)
        row = store.get_query(qid)
        assert row["status"] == "completed"
        assert row["answer"] == "the answer"
        assert row["duration_secs"] == pytest.approx(2.5)
        assert row["completed_at"] is not None

    def test_fail_sets_error(self, store):
        qid = store.insert_query("test prompt")
        store.fail_query(qid, "something broke")
        row = store.get_query(qid)
        assert row["status"] == "failed"
        assert row["error"] == "something broke"

    def test_get_nonexistent_returns_none(self, store):
        assert store.get_query(9999) is None

    def test_insert_cached_query(self, store):
        # Create a source query
        src_id = store.insert_query("original prompt")
        store.complete_query(src_id, "original answer", "[]", 3.0)

        # Create a cached entry pointing to it
        cached_id = store.insert_cached_query(
            "similar prompt", "original answer", "[]", src_id, 3.0
        )
        row = store.get_query(cached_id)
        assert row["status"] == "cached"
        assert row["source_query_id"] == src_id
        assert row["answer"] == "original answer"

    def test_get_query_parses_evidence(self, store):
        qid = store.insert_query("prompt")
        evidence = '[{"tool":"read_map","args":{},"summary":"Read the map"}]'
        store.complete_query(qid, "answer", evidence, 1.0)
        row = store.get_query(qid)
        assert isinstance(row["evidence"], list)
        assert row["evidence"][0]["tool"] == "read_map"

    def test_get_query_malformed_evidence(self, store):
        qid = store.insert_query("prompt")
        store.complete_query(qid, "answer", "not json", 1.0)
        # get_query catches JSONDecodeError and leaves evidence as-is
        row = store.get_query(qid)
        assert row["evidence"] == "not json"


# ── SqliteStore completed query filtering tests ──


class TestSqliteCompletedQueries:
    """Tests for get_completed_queries_since and list_queries."""

    def test_since_none_returns_all_completed(self, store):
        q1 = store.insert_query("completed one")
        store.complete_query(q1, "answer", "[]", 1.0)
        q2 = store.insert_query("failed one")
        store.fail_query(q2, "err")
        store.insert_query("running one")  # left as running

        results = store.get_completed_queries_since(None)
        assert len(results) == 1
        assert results[0]["prompt"] == "completed one"

    def test_since_timestamp_filters(self, store):
        q1 = store.insert_query("first")
        store.complete_query(q1, "answer-1", "[]", 1.0)

        time.sleep(0.05)
        # Record a timestamp between the two completions
        from datetime import datetime, timezone
        cutoff = datetime.now(timezone.utc).isoformat()
        time.sleep(0.05)

        q2 = store.insert_query("second")
        store.complete_query(q2, "answer-2", "[]", 1.0)

        results = store.get_completed_queries_since(cutoff)
        assert len(results) == 1
        assert results[0]["prompt"] == "second"

    def test_excludes_failed_running_cached(self, store):
        # Completed
        q1 = store.insert_query("completed")
        store.complete_query(q1, "answer", "[]", 1.0)
        # Failed
        q2 = store.insert_query("failed")
        store.fail_query(q2, "err")
        # Running (no update)
        store.insert_query("running")
        # Cached
        store.insert_cached_query("cached", "answer", "[]", q1, 1.0)

        results = store.get_completed_queries_since(None)
        assert len(results) == 1
        assert results[0]["prompt"] == "completed"

    def test_list_queries_order(self, store):
        for i in range(3):
            store.insert_query(f"query-{i}")
            time.sleep(0.02)

        rows = store.list_queries()
        # Newest first
        assert rows[0]["prompt"] == "query-2"
        assert rows[-1]["prompt"] == "query-0"

    def test_list_queries_limit(self, store):
        for i in range(5):
            store.insert_query(f"query-{i}")
        rows = store.list_queries(limit=2)
        assert len(rows) == 2


# ── Dashboard cache integration tests ──


class TestDashboardCacheIntegration:
    """Integration tests for the /dashboard/api/run/query cache logic.

    Uses real SQLite but mocks the agent loop to avoid Gemini API calls.
    """

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Patch config paths and reset task manager for each test."""
        self.db_path = tmp_path / "test.db"
        self.store = SqliteStore(self.db_path)
        self.store.init_db()

        # Patch config so dashboard creates a store pointing at our temp DB
        self._patches = [
            patch("indiseek.config.SQLITE_PATH", self.db_path),
            patch("indiseek.config.GEMINI_API_KEY", "fake-key"),
        ]
        for p in self._patches:
            p.start()

        # Reset the task manager between tests
        from indiseek.api.dashboard import _task_manager
        _task_manager._tasks.clear()

        yield

        for p in self._patches:
            p.stop()

    def _client(self):
        from indiseek.api.server import app
        from fastapi.testclient import TestClient
        return TestClient(app)

    def _insert_completed(self, prompt="How does HMR work?", answer="HMR answer",
                          evidence='[{"tool":"read_map","args":{},"summary":"s"}]',
                          duration=2.0):
        """Helper: insert a completed query and return its id."""
        qid = self.store.insert_query(prompt)
        self.store.complete_query(qid, answer, evidence, duration)
        return qid

    # -- Cache miss --

    def test_cache_miss_first_query(self):
        """No completed queries → cache miss → background task submitted."""
        with patch("indiseek.agent.loop.create_agent_loop"):
            client = self._client()
            resp = client.post("/dashboard/api/run/query", json={"prompt": "What is X?"})
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data.get("cached") is not True

    # -- Cache hits --

    def test_cache_hit_identical(self):
        """Identical prompt → cache hit with correct source_query_id."""
        src_id = self._insert_completed("How does HMR work?", "HMR answer")
        client = self._client()
        resp = client.post("/dashboard/api/run/query", json={"prompt": "How does HMR work?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is True
        assert data["source_query_id"] == src_id
        assert data["answer"] == "HMR answer"

    def test_cache_hit_similar(self):
        """Same tokens, different case → cache hit."""
        src_id = self._insert_completed("How does HMR work in Vite")
        client = self._client()
        resp = client.post("/dashboard/api/run/query", json={"prompt": "how does hmr work in vite"})
        data = resp.json()
        assert data["cached"] is True
        assert data["source_query_id"] == src_id

    def test_cache_miss_dissimilar(self):
        """Unrelated prompt → cache miss."""
        self._insert_completed("How does HMR work?")
        with patch("indiseek.agent.loop.create_agent_loop"):
            client = self._client()
            resp = client.post("/dashboard/api/run/query",
                               json={"prompt": "What is the plugin API architecture?"})
        data = resp.json()
        assert data.get("cached") is not True
        assert "task_id" in data

    # -- Force bypass --

    def test_force_bypasses_cache(self):
        """force=True skips cache even for identical prompt."""
        self._insert_completed("How does HMR work?")
        with patch("indiseek.agent.loop.create_agent_loop"):
            client = self._client()
            resp = client.post("/dashboard/api/run/query",
                               json={"prompt": "How does HMR work?", "force": True})
        data = resp.json()
        assert data.get("cached") is not True
        assert "task_id" in data

    # -- Failed queries --

    def test_failed_query_not_cache_source(self):
        """Failed queries are never returned by get_completed_queries_since."""
        qid = self.store.insert_query("How does HMR work?")
        self.store.fail_query(qid, "agent crashed")
        with patch("indiseek.agent.loop.create_agent_loop"):
            client = self._client()
            resp = client.post("/dashboard/api/run/query",
                               json={"prompt": "How does HMR work?"})
        data = resp.json()
        assert data.get("cached") is not True

    # -- Reindex invalidation --

    def test_reindex_invalidates(self):
        """Queries completed before last_index_at are not cache candidates."""
        self._insert_completed("How does HMR work?")
        time.sleep(0.05)
        # Simulate reindex
        from datetime import datetime, timezone
        self.store.set_metadata("last_index_at", datetime.now(timezone.utc).isoformat())

        with patch("indiseek.agent.loop.create_agent_loop"):
            client = self._client()
            resp = client.post("/dashboard/api/run/query",
                               json={"prompt": "How does HMR work?"})
        data = resp.json()
        assert data.get("cached") is not True

    # -- Best match selection --

    def test_best_match_wins(self):
        """When multiple candidates match, the highest similarity wins."""
        # Insert two completed queries
        id_close = self._insert_completed("alpha beta gamma delta")
        self._insert_completed("completely unrelated stuff here")

        client = self._client()
        resp = client.post("/dashboard/api/run/query",
                           json={"prompt": "alpha beta gamma delta epsilon"})
        data = resp.json()
        # 4/5 = 0.8 — at threshold, should hit
        assert data["cached"] is True
        assert data["source_query_id"] == id_close

    # -- Response shape --

    def test_cache_response_shape(self):
        """Cache hit response has all expected keys with correct types."""
        self._insert_completed()
        client = self._client()
        resp = client.post("/dashboard/api/run/query", json={"prompt": "How does HMR work?"})
        data = resp.json()
        assert data["cached"] is True
        assert isinstance(data["query_id"], int)
        assert isinstance(data["source_query_id"], int)
        assert isinstance(data["answer"], str)
        assert isinstance(data["evidence"], list)

    # -- Cache hit bypasses running task check --

    def test_cache_hit_bypasses_running_task_check(self):
        """Cache hit should return even when a task is already running (no 409)."""
        self._insert_completed("How does HMR work?")
        from indiseek.api.dashboard import _task_manager
        with patch.object(_task_manager, "has_running_task", return_value=True):
            client = self._client()
            resp = client.post("/dashboard/api/run/query",
                               json={"prompt": "How does HMR work?"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["cached"] is True

    # -- Edge cases --

    def test_empty_answer_cached(self):
        """Completed query with empty answer string can serve as cache source."""
        self._insert_completed("How does HMR work?", answer="")
        client = self._client()
        resp = client.post("/dashboard/api/run/query", json={"prompt": "How does HMR work?"})
        data = resp.json()
        assert data["cached"] is True
        assert data["answer"] == ""

    def test_malformed_evidence_in_source(self):
        """Malformed evidence JSON in source query → cache hit returns evidence: []."""
        self._insert_completed("How does HMR work?", evidence="not json")
        client = self._client()
        resp = client.post("/dashboard/api/run/query", json={"prompt": "How does HMR work?"})
        data = resp.json()
        assert data["cached"] is True
        assert data["evidence"] == []

    def test_cache_creates_new_query_row(self):
        """Each cache hit creates a new query row with status='cached'."""
        src_id = self._insert_completed("How does HMR work?")
        client = self._client()
        resp = client.post("/dashboard/api/run/query", json={"prompt": "How does HMR work?"})
        data = resp.json()
        cached_id = data["query_id"]
        assert cached_id != src_id

        row = self.store.get_query(cached_id)
        assert row["status"] == "cached"
        assert row["source_query_id"] == src_id
