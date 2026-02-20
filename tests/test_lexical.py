"""Tests for lexical (Tantivy BM25) indexer and hybrid search."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from indiseek.storage.sqlite_store import Chunk

if TYPE_CHECKING:
    from indiseek.storage.sqlite_store import SqliteStore


# ── Fixtures ──


@pytest.fixture
def populated_store(store: SqliteStore) -> SqliteStore:
    """Store with sample chunks for testing."""
    chunks = [
        Chunk(None, "server/hmr.ts", "handleHMRUpdate", "function", 1, 20,
              "export function handleHMRUpdate(module: string) {\n  propagateUpdate(module)\n}", 15),
        Chunk(None, "server/hmr.ts", "propagateUpdate", "function", 22, 40,
              "function propagateUpdate(module: string) {\n  // CSS hot reload\n  if (isCSSModule(module)) reload()\n}", 20),
        Chunk(None, "client/css.ts", "updateCSS", "function", 1, 15,
              "export function updateCSS(link: HTMLLinkElement) {\n  link.href = link.href.split('?')[0] + '?' + Date.now()\n}", 18),
        Chunk(None, "config/resolve.ts", "resolveConfig", "function", 1, 50,
              "export async function resolveConfig(config: UserConfig): Promise<ResolvedConfig> {\n  // merge defaults\n}", 20),
        Chunk(None, "plugins/css.ts", None, "module", 1, 10,
              "import { createFilter } from '@rollup/pluginutils'\nimport postcss from 'postcss'", 12),
    ]
    store.insert_chunks(chunks)
    return store


# ── LexicalIndexer tests ──


class TestLexicalIndexer:
    def test_build_empty_index(self, store: SqliteStore, tmp_path: Path) -> None:
        from indiseek.indexer.lexical import LexicalIndexer

        indexer = LexicalIndexer(store, tmp_path / "tantivy")
        count = indexer.build_index()
        assert count == 0

    def test_build_index_counts(self, populated_store: SqliteStore, tmp_path: Path) -> None:
        from indiseek.indexer.lexical import LexicalIndexer

        indexer = LexicalIndexer(populated_store, tmp_path / "tantivy")
        count = indexer.build_index()
        assert count == 5

    def test_search_exact_identifier(self, populated_store: SqliteStore, tmp_path: Path) -> None:
        from indiseek.indexer.lexical import LexicalIndexer

        indexer = LexicalIndexer(populated_store, tmp_path / "tantivy")
        indexer.build_index()

        results = indexer.search("handleHMRUpdate", limit=5)
        assert len(results) > 0
        assert results[0].file_path == "server/hmr.ts"
        assert results[0].symbol_name == "handleHMRUpdate"

    def test_search_stemmed_query(self, populated_store: SqliteStore, tmp_path: Path) -> None:
        """en_stem tokenizer should match related word forms (e.g. 'reloading' -> 'reload')."""
        from indiseek.indexer.lexical import LexicalIndexer

        indexer = LexicalIndexer(populated_store, tmp_path / "tantivy")
        indexer.build_index()

        # "reloading" should stem to "reload" and match "reload()" in chunk content
        results = indexer.search("reloading", limit=5)
        assert len(results) > 0
        file_paths = {r.file_path for r in results}
        assert "server/hmr.ts" in file_paths

    def test_search_no_results(self, populated_store: SqliteStore, tmp_path: Path) -> None:
        from indiseek.indexer.lexical import LexicalIndexer

        indexer = LexicalIndexer(populated_store, tmp_path / "tantivy")
        indexer.build_index()

        results = indexer.search("zzzznonexistentterm", limit=5)
        assert len(results) == 0

    def test_search_limit(self, populated_store: SqliteStore, tmp_path: Path) -> None:
        from indiseek.indexer.lexical import LexicalIndexer

        indexer = LexicalIndexer(populated_store, tmp_path / "tantivy")
        indexer.build_index()

        results = indexer.search("function", limit=2)
        assert len(results) <= 2

    def test_search_result_fields(self, populated_store: SqliteStore, tmp_path: Path) -> None:
        from indiseek.indexer.lexical import LexicalIndexer

        indexer = LexicalIndexer(populated_store, tmp_path / "tantivy")
        indexer.build_index()

        results = indexer.search("resolveConfig", limit=1)
        assert len(results) == 1
        r = results[0]
        assert r.file_path == "config/resolve.ts"
        assert r.symbol_name == "resolveConfig"
        assert r.chunk_type == "function"
        assert r.start_line == 1
        assert r.end_line == 50
        assert r.score > 0.0
        assert "resolveConfig" in r.content

    def test_rebuild_index_replaces_old(self, populated_store: SqliteStore, tmp_path: Path) -> None:
        """Rebuilding the index should wipe and recreate."""
        from indiseek.indexer.lexical import LexicalIndexer

        indexer = LexicalIndexer(populated_store, tmp_path / "tantivy")
        count1 = indexer.build_index()
        assert count1 == 5

        # Build again — should be same count, not doubled
        count2 = indexer.build_index()
        assert count2 == 5

    def test_open_existing_index(self, populated_store: SqliteStore, tmp_path: Path) -> None:
        """Can open and search an index built by a previous instance."""
        from indiseek.indexer.lexical import LexicalIndexer

        index_path = tmp_path / "tantivy"
        indexer1 = LexicalIndexer(populated_store, index_path)
        indexer1.build_index()

        # New instance pointing at same path
        indexer2 = LexicalIndexer(populated_store, index_path)
        indexer2.open_index()
        results = indexer2.search("CSS", limit=5)
        assert len(results) > 0

    def test_open_nonexistent_raises(self, store: SqliteStore, tmp_path: Path) -> None:
        from indiseek.indexer.lexical import LexicalIndexer

        indexer = LexicalIndexer(store, tmp_path / "no_such_index")
        with pytest.raises(FileNotFoundError):
            indexer.open_index()

    def test_null_symbol_name(self, populated_store: SqliteStore, tmp_path: Path) -> None:
        """Chunks with no symbol_name should be searchable and return None."""
        from indiseek.indexer.lexical import LexicalIndexer

        indexer = LexicalIndexer(populated_store, tmp_path / "tantivy")
        indexer.build_index()

        results = indexer.search("postcss", limit=5)
        assert len(results) > 0
        module_results = [r for r in results if r.chunk_type == "module"]
        assert len(module_results) > 0
        assert module_results[0].symbol_name is None


# ── Hybrid search tests ──


class TestHybridSearch:
    def test_lexical_only(self, populated_store: SqliteStore, tmp_path: Path) -> None:
        from indiseek.indexer.lexical import LexicalIndexer
        from indiseek.tools.search_code import CodeSearcher

        indexer = LexicalIndexer(populated_store, tmp_path / "tantivy")
        indexer.build_index()

        searcher = CodeSearcher(lexical_indexer=indexer)
        results = searcher.search("handleHMRUpdate", mode="lexical", limit=5)
        assert len(results) > 0
        assert results[0].match_type == "lexical"
        assert results[0].file_path == "server/hmr.ts"

    def test_semantic_only_requires_embed_fn(self, tmp_path: Path) -> None:
        from indiseek.tools.search_code import CodeSearcher

        searcher = CodeSearcher()
        with pytest.raises(RuntimeError, match="Semantic search requires"):
            searcher.search("query", mode="semantic")

    def test_lexical_only_requires_indexer(self) -> None:
        from indiseek.tools.search_code import CodeSearcher

        searcher = CodeSearcher()
        with pytest.raises(RuntimeError, match="Lexical search requires"):
            searcher.search("query", mode="lexical")

    def test_invalid_mode(self) -> None:
        from indiseek.tools.search_code import CodeSearcher

        searcher = CodeSearcher()
        with pytest.raises(ValueError, match="Unknown search mode"):
            searcher.search("query", mode="invalid")

    def test_hybrid_lexical_only_fallback(
        self, populated_store: SqliteStore, tmp_path: Path
    ) -> None:
        """Hybrid with only lexical backend should fall back to lexical."""
        from indiseek.indexer.lexical import LexicalIndexer
        from indiseek.tools.search_code import CodeSearcher

        indexer = LexicalIndexer(populated_store, tmp_path / "tantivy")
        indexer.build_index()

        searcher = CodeSearcher(lexical_indexer=indexer)
        results = searcher.search("CSS reload", mode="hybrid", limit=5)
        assert len(results) > 0
        assert all(r.match_type == "lexical" for r in results)

    def test_hybrid_with_both_backends(
        self, populated_store: SqliteStore, tmp_path: Path
    ) -> None:
        """Hybrid search with both semantic and lexical backends."""
        import hashlib

        from indiseek.indexer.embedder import Embedder
        from indiseek.indexer.lexical import LexicalIndexer
        from indiseek.storage.vector_store import VectorStore
        from indiseek.tools.search_code import CodeSearcher

        # Build lexical index
        lexical = LexicalIndexer(populated_store, tmp_path / "tantivy")
        lexical.build_index()

        # Build semantic index with fake embeddings
        def fake_embed(texts: list[str]) -> list[list[float]]:
            results = []
            for t in texts:
                h = hashlib.md5(t.encode()).digest()
                vec = [float(b) / 255.0 for b in h[:8]]
                results.append(vec)
            return results

        from unittest.mock import MagicMock
        provider = MagicMock()
        provider.embed = MagicMock(side_effect=fake_embed)

        vs = VectorStore(tmp_path / "lancedb", dims=8)
        embedder = Embedder(populated_store, vs, provider=provider, batch_size=10)
        embedder.embed_all_chunks()

        def embed_query(text: str) -> list[float]:
            return fake_embed([text])[0]

        searcher = CodeSearcher(
            vector_store=vs,
            lexical_indexer=lexical,
            embed_fn=embed_query,
        )
        results = searcher.search("CSS update function", mode="hybrid", limit=5)
        assert len(results) > 0
        # Results should have various match types
        match_types = {r.match_type for r in results}
        assert len(match_types) >= 1  # at least one type present

    def test_rrf_deduplicates(self) -> None:
        """RRF should merge results appearing in both lists."""
        from indiseek.indexer.lexical import LexicalResult
        from indiseek.storage.vector_store import SearchResult as SemanticResult
        from indiseek.tools.search_code import _reciprocal_rank_fusion

        semantic = [
            SemanticResult(chunk_id=1, file_path="a.ts", symbol_name="foo",
                           chunk_type="function", content="code1", score=0.1),
            SemanticResult(chunk_id=2, file_path="b.ts", symbol_name="bar",
                           chunk_type="function", content="code2", score=0.5),
        ]
        lexical = [
            LexicalResult(chunk_id=2, file_path="b.ts", symbol_name="bar",
                          chunk_type="function", content="code2",
                          start_line=1, end_line=10, score=5.0),
            LexicalResult(chunk_id=3, file_path="c.ts", symbol_name="baz",
                          chunk_type="function", content="code3",
                          start_line=1, end_line=5, score=3.0),
        ]

        fused = _reciprocal_rank_fusion(semantic, lexical)

        # chunk_id=2 appears in both, should be "hybrid" and rank highest
        assert fused[0].chunk_id == 2
        assert fused[0].match_type == "hybrid"

        # All 3 unique chunk_ids should appear
        chunk_ids = {r.chunk_id for r in fused}
        assert chunk_ids == {1, 2, 3}

    def test_rrf_scores_are_descending(self) -> None:
        """RRF results should be sorted by score descending."""
        from indiseek.indexer.lexical import LexicalResult
        from indiseek.storage.vector_store import SearchResult as SemanticResult
        from indiseek.tools.search_code import _reciprocal_rank_fusion

        semantic = [
            SemanticResult(chunk_id=i, file_path=f"f{i}.ts", symbol_name=f"s{i}",
                           chunk_type="function", content=f"code{i}", score=float(i))
            for i in range(1, 6)
        ]
        lexical = [
            LexicalResult(chunk_id=i, file_path=f"f{i}.ts", symbol_name=f"s{i}",
                          chunk_type="function", content=f"code{i}",
                          start_line=1, end_line=10, score=float(i))
            for i in range(5, 0, -1)
        ]

        fused = _reciprocal_rank_fusion(semantic, lexical)
        scores = [r.score for r in fused]
        assert scores == sorted(scores, reverse=True)
