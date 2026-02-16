"""Hybrid code search combining semantic (LanceDB) and lexical (Tantivy) results."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from indiseek.indexer.lexical import LexicalIndexer, LexicalResult
from indiseek.storage.vector_store import SearchResult as SemanticResult
from indiseek.storage.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class HybridResult:
    """Unified search result from hybrid, semantic, or lexical search."""

    chunk_id: int
    file_path: str
    symbol_name: str | None
    chunk_type: str
    content: str
    score: float
    match_type: str  # "semantic", "lexical", or "hybrid"


def _reciprocal_rank_fusion(
    semantic_results: list[SemanticResult],
    lexical_results: list[LexicalResult],
    k: int = 60,
) -> list[HybridResult]:
    """Merge semantic and lexical results using Reciprocal Rank Fusion (RRF).

    RRF score = sum(1 / (k + rank)) across all result lists where the item appears.
    Higher score = more relevant.
    """
    # Build a map keyed by chunk_id -> accumulated RRF score + metadata
    scores: dict[int, float] = {}
    metadata: dict[int, dict] = {}

    # Score semantic results
    for rank, r in enumerate(semantic_results):
        rrf = 1.0 / (k + rank + 1)
        scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + rrf
        if r.chunk_id not in metadata:
            metadata[r.chunk_id] = {
                "file_path": r.file_path,
                "symbol_name": r.symbol_name,
                "chunk_type": r.chunk_type,
                "content": r.content,
                "match_type": "semantic",
            }

    # Score lexical results
    for rank, r in enumerate(lexical_results):
        rrf = 1.0 / (k + rank + 1)
        scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + rrf
        if r.chunk_id in metadata:
            # Appeared in both — mark as hybrid
            metadata[r.chunk_id]["match_type"] = "hybrid"
        else:
            metadata[r.chunk_id] = {
                "file_path": r.file_path,
                "symbol_name": r.symbol_name,
                "chunk_type": r.chunk_type,
                "content": r.content,
                "match_type": "lexical",
            }

    # Sort by RRF score descending
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    return [
        HybridResult(
            chunk_id=chunk_id,
            score=score,
            **metadata[chunk_id],
        )
        for chunk_id, score in ranked
    ]


def format_results(results: list[HybridResult], query: str) -> str:
    """Format search results for LLM consumption.

    Args:
        results: List of HybridResult from CodeSearcher.search().
        query: The original query string.

    Returns:
        Formatted string with file paths, line ranges, content snippets, and scores.
    """
    if not results:
        return f"No results found for '{query}'."

    lines = [f"Search results for '{query}' ({len(results)} result(s)):"]
    lines.append("")

    for i, r in enumerate(results, 1):
        symbol_info = f" [{r.symbol_name}]" if r.symbol_name else ""
        lines.append(f"  {i}. {r.file_path}{symbol_info} ({r.chunk_type}, {r.match_type})")
        lines.append(f"     Score: {r.score:.4f}")
        # Show a truncated content preview
        preview = r.content.strip()
        if len(preview) > 300:
            preview = preview[:300] + "..."
        for pl in preview.splitlines()[:8]:
            lines.append(f"     | {pl}")
        if len(preview.splitlines()) > 8:
            lines.append(f"     | ... ({len(preview.splitlines())} lines total)")
        lines.append("")

    return "\n".join(lines)


class CodeSearcher:
    """Searches code using semantic, lexical, or hybrid mode."""

    def __init__(
        self,
        vector_store: VectorStore | None = None,
        lexical_indexer: LexicalIndexer | None = None,
        embed_fn: callable | None = None,  # type: ignore[type-arg]
    ) -> None:
        """Initialize with available search backends.

        Args:
            vector_store: LanceDB vector store for semantic search.
            lexical_indexer: Tantivy indexer for lexical search.
            embed_fn: Function that embeds a query string into a vector.
                      Signature: (text: str) -> list[float]
        """
        self._vector_store = vector_store
        self._lexical_indexer = lexical_indexer
        self._embed_fn = embed_fn

    def search(
        self,
        query: str,
        mode: str = "hybrid",
        limit: int = 10,
    ) -> list[HybridResult]:
        """Search code across indexes.

        Args:
            query: Search query string.
            mode: One of "hybrid" (default), "semantic", "lexical".
            limit: Max results to return.

        Returns:
            List of HybridResult sorted by relevance.
        """
        if mode == "semantic":
            return self._semantic_search(query, limit)
        elif mode == "lexical":
            return self._lexical_search(query, limit)
        elif mode == "hybrid":
            return self._hybrid_search(query, limit)
        else:
            raise ValueError(f"Unknown search mode: {mode!r}. Use 'hybrid', 'semantic', or 'lexical'.")

    def _semantic_search(self, query: str, limit: int) -> list[HybridResult]:
        if self._vector_store is None or self._embed_fn is None:
            raise RuntimeError("Semantic search requires vector_store and embed_fn")
        t0 = time.perf_counter()
        query_vector = self._embed_fn(query)
        embed_ms = (time.perf_counter() - t0) * 1000
        t1 = time.perf_counter()
        results = self._vector_store.search(query_vector, limit=limit)
        search_ms = (time.perf_counter() - t1) * 1000
        logger.debug("semantic: embed %.0fms, search %.0fms, %d results", embed_ms, search_ms, len(results))
        return [
            HybridResult(
                chunk_id=r.chunk_id,
                file_path=r.file_path,
                symbol_name=r.symbol_name,
                chunk_type=r.chunk_type,
                content=r.content,
                score=r.score,
                match_type="semantic",
            )
            for r in results
        ]

    def _lexical_search(self, query: str, limit: int) -> list[HybridResult]:
        if self._lexical_indexer is None:
            raise RuntimeError("Lexical search requires lexical_indexer")
        t0 = time.perf_counter()
        results = self._lexical_indexer.search(query, limit=limit)
        logger.debug("lexical: %.0fms, %d results", (time.perf_counter() - t0) * 1000, len(results))
        return [
            HybridResult(
                chunk_id=r.chunk_id,
                file_path=r.file_path,
                symbol_name=r.symbol_name,
                chunk_type=r.chunk_type,
                content=r.content,
                score=r.score,
                match_type="lexical",
            )
            for r in results
        ]

    def _hybrid_search(self, query: str, limit: int) -> list[HybridResult]:
        # Fetch more than needed from each backend, then fuse and trim
        fetch_limit = limit * 2

        semantic_results: list[SemanticResult] = []
        lexical_results: list[LexicalResult] = []

        if self._vector_store is not None and self._embed_fn is not None:
            t0 = time.perf_counter()
            query_vector = self._embed_fn(query)
            embed_ms = (time.perf_counter() - t0) * 1000
            t1 = time.perf_counter()
            semantic_results = self._vector_store.search(query_vector, limit=fetch_limit)
            search_ms = (time.perf_counter() - t1) * 1000
            logger.debug("hybrid/semantic: embed %.0fms, search %.0fms, %d results", embed_ms, search_ms, len(semantic_results))

        if self._lexical_indexer is not None:
            t0 = time.perf_counter()
            lexical_results = self._lexical_indexer.search(query, limit=fetch_limit)
            logger.debug("hybrid/lexical: %.0fms, %d results", (time.perf_counter() - t0) * 1000, len(lexical_results))

        if not semantic_results and not lexical_results:
            logger.debug("hybrid: no results from either backend")
            return []

        # If only one backend available, fall through to single-mode
        if not semantic_results:
            logger.debug("hybrid: falling back to lexical-only")
            return self._lexical_search(query, limit)
        if not lexical_results:
            logger.debug("hybrid: falling back to semantic-only")
            return self._semantic_search(query, limit)

        fused = _reciprocal_rank_fusion(semantic_results, lexical_results)
        hybrid_count = sum(1 for r in fused[:limit] if r.match_type == "hybrid")
        logger.debug("hybrid/RRF: %d fused -> %d returned (%d in both backends)", len(fused), min(limit, len(fused)), hybrid_count)
        return fused[:limit]
