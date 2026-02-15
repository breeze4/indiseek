"""Tests for vector storage, embedder, and provider."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from indiseek.storage.sqlite_store import Chunk, SqliteStore
from indiseek.storage.vector_store import VectorStore


# ── Fixtures ──


@pytest.fixture
def store(tmp_path: Path) -> SqliteStore:
    s = SqliteStore(tmp_path / "test.db")
    s.init_db()
    return s


@pytest.fixture
def vector_store(tmp_path: Path) -> VectorStore:
    vs = VectorStore(tmp_path / "lancedb", dims=8)
    vs.init_table()
    return vs


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Return deterministic fake embeddings based on text length."""
    import hashlib

    results = []
    for t in texts:
        h = hashlib.md5(t.encode()).digest()
        vec = [float(b) / 255.0 for b in h[:8]]
        results.append(vec)
    return results


@pytest.fixture
def mock_provider() -> MagicMock:
    provider = MagicMock()
    provider.embed = MagicMock(side_effect=_fake_embed)
    return provider


# ── VectorStore tests ──


class TestVectorStore:
    def test_init_creates_table(self, vector_store: VectorStore) -> None:
        assert vector_store.count() == 0

    def test_add_and_count(self, vector_store: VectorStore) -> None:
        vector_store.add_chunks(
            vectors=[[0.1] * 8, [0.2] * 8],
            chunk_ids=[1, 2],
            file_paths=["a.ts", "b.ts"],
            symbol_names=["foo", None],
            chunk_types=["function", "module"],
            contents=["function foo() {}", "import x from 'y'"],
        )
        assert vector_store.count() == 2

    def test_search_returns_results(self, vector_store: VectorStore) -> None:
        vector_store.add_chunks(
            vectors=[[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]],
            chunk_ids=[1],
            file_paths=["a.ts"],
            symbol_names=["foo"],
            chunk_types=["function"],
            contents=["function foo() {}"],
        )
        results = vector_store.search([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], limit=5)
        assert len(results) == 1
        assert results[0].chunk_id == 1
        assert results[0].file_path == "a.ts"
        assert results[0].symbol_name == "foo"
        assert results[0].content == "function foo() {}"

    def test_search_ranking(self, vector_store: VectorStore) -> None:
        """Closer vectors should rank higher (lower distance)."""
        vector_store.add_chunks(
            vectors=[
                [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # close to query
                [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],  # far from query
            ],
            chunk_ids=[1, 2],
            file_paths=["close.ts", "far.ts"],
            symbol_names=["close", "far"],
            chunk_types=["function", "function"],
            contents=["close code", "far code"],
        )
        results = vector_store.search(
            [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], limit=2
        )
        assert results[0].file_path == "close.ts"
        assert results[0].score < results[1].score

    def test_search_limit(self, vector_store: VectorStore) -> None:
        for i in range(10):
            vector_store.add_chunks(
                vectors=[[float(i) / 10] * 8],
                chunk_ids=[i],
                file_paths=[f"file{i}.ts"],
                symbol_names=[f"sym{i}"],
                chunk_types=["function"],
                contents=[f"content {i}"],
            )
        results = vector_store.search([0.5] * 8, limit=3)
        assert len(results) == 3

    def test_null_symbol_name_stored_as_empty(self, vector_store: VectorStore) -> None:
        vector_store.add_chunks(
            vectors=[[0.5] * 8],
            chunk_ids=[1],
            file_paths=["a.ts"],
            symbol_names=[None],
            chunk_types=["module"],
            contents=["content"],
        )
        results = vector_store.search([0.5] * 8, limit=1)
        assert results[0].symbol_name is None  # converted back from ""

    def test_reinit_opens_existing_table(self, tmp_path: Path) -> None:
        """Creating a second VectorStore on the same path should open the existing table."""
        vs1 = VectorStore(tmp_path / "lancedb", dims=8)
        vs1.init_table()
        vs1.add_chunks(
            vectors=[[0.1] * 8],
            chunk_ids=[1],
            file_paths=["a.ts"],
            symbol_names=["foo"],
            chunk_types=["function"],
            contents=["content"],
        )
        assert vs1.count() == 1

        vs2 = VectorStore(tmp_path / "lancedb", dims=8)
        vs2.init_table()
        assert vs2.count() == 1


# ── Embedder tests ──


class TestEmbedder:
    def test_embed_all_chunks(
        self,
        store: SqliteStore,
        tmp_path: Path,
        mock_provider: MagicMock,
    ) -> None:
        from indiseek.indexer.embedder import Embedder

        # Insert some chunks into SQLite
        chunks = [
            Chunk(None, "a.ts", "foo", "function", 1, 10, "function foo() { return 1; }", 7),
            Chunk(None, "a.ts", "bar", "function", 11, 20, "function bar() { return 2; }", 7),
            Chunk(None, "b.ts", None, "module", 1, 5, "import x from 'y';", 5),
        ]
        store.insert_chunks(chunks)

        vs = VectorStore(tmp_path / "lancedb", dims=8)
        embedder = Embedder(store, vs, provider=mock_provider, batch_size=2)
        count = embedder.embed_all_chunks()

        assert count == 3
        assert vs.count() == 3
        assert mock_provider.embed.call_count == 2  # 2 chunks + 1 chunk = 2 batches

    def test_embed_empty_db(
        self,
        store: SqliteStore,
        tmp_path: Path,
        mock_provider: MagicMock,
    ) -> None:
        from indiseek.indexer.embedder import Embedder

        vs = VectorStore(tmp_path / "lancedb", dims=8)
        embedder = Embedder(store, vs, provider=mock_provider)
        count = embedder.embed_all_chunks()

        assert count == 0
        mock_provider.embed.assert_not_called()

    def test_embed_searchable(
        self,
        store: SqliteStore,
        tmp_path: Path,
        mock_provider: MagicMock,
    ) -> None:
        """After embedding, chunks should be searchable."""
        from indiseek.indexer.embedder import Embedder

        chunks = [
            Chunk(None, "hmr.ts", "handleHMR", "function", 1, 20, "function handleHMR() { propagate(); }", 9),
            Chunk(None, "css.ts", "updateCSS", "function", 1, 10, "function updateCSS() { reload(); }", 8),
        ]
        store.insert_chunks(chunks)

        vs = VectorStore(tmp_path / "lancedb", dims=8)
        embedder = Embedder(store, vs, provider=mock_provider, batch_size=10)
        embedder.embed_all_chunks()

        # Search with a query vector
        query_vec = _fake_embed(["HMR propagation"])[0]
        results = vs.search(query_vec, limit=2)
        assert len(results) == 2
        assert all(r.file_path in ("hmr.ts", "css.ts") for r in results)

    def test_embed_batch_error_retries(
        self,
        store: SqliteStore,
        tmp_path: Path,
    ) -> None:
        """If embed fails once, it retries and succeeds."""
        from indiseek.indexer.embedder import Embedder

        call_count = 0

        def flaky_embed(texts: list[str]) -> list[list[float]]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("API error")
            return _fake_embed(texts)

        provider = MagicMock()
        provider.embed = MagicMock(side_effect=flaky_embed)

        chunks = [Chunk(None, "a.ts", "foo", "function", 1, 5, "code", 2)]
        store.insert_chunks(chunks)

        vs = VectorStore(tmp_path / "lancedb", dims=8)
        embedder = Embedder(store, vs, provider=provider, batch_size=10)
        count = embedder.embed_all_chunks()

        assert count == 1
        assert provider.embed.call_count == 2  # original + retry


# ── Provider tests (unit, no API calls) ──


class TestGeminiProviderInit:
    def test_provider_accepts_custom_config(self) -> None:
        """GeminiProvider can be constructed with custom params (doesn't call API)."""
        from indiseek.agent.provider import GeminiProvider

        # Just test construction — no API call
        provider = GeminiProvider(
            api_key="test-key",
            embedding_model="test-model",
            embedding_dims=256,
        )
        assert provider._embedding_model == "test-model"
        assert provider._embedding_dims == 256
