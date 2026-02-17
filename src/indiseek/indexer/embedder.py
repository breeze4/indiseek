"""Embed AST-scoped code chunks via Gemini and store in LanceDB."""

from __future__ import annotations

import sys
import time
from typing import Callable

from indiseek.agent.provider import EmbeddingProvider, GeminiProvider
from indiseek.storage.sqlite_store import SqliteStore
from indiseek.storage.vector_store import VectorStore


# Gemini allows up to 250 texts per request, but we use a conservative batch
# size to stay well within token limits (each chunk can be large).
DEFAULT_BATCH_SIZE = 20


class Embedder:
    """Reads chunks from SQLite, embeds them, and stores vectors in LanceDB."""

    def __init__(
        self,
        store: SqliteStore,
        vector_store: VectorStore,
        provider: EmbeddingProvider | None = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._store = store
        self._vector_store = vector_store
        self._provider = provider or GeminiProvider()
        self._batch_size = batch_size

    def embed_all_chunks(
        self,
        path_filter: str | None = None,
        on_progress: Callable[[dict], None] | None = None,
    ) -> int:
        """Embed chunks from SQLite that aren't already in LanceDB.

        Args:
            path_filter: Optional file path prefix to restrict which chunks are embedded.
            on_progress: Optional callback for progress events.

        Returns the total number of chunks embedded.
        """
        # Load all chunks from SQLite
        if path_filter:
            cur = self._store._conn.execute(
                "SELECT id, file_path, symbol_name, chunk_type, content FROM chunks "
                "WHERE file_path LIKE ?",
                (path_filter + "%",),
            )
        else:
            cur = self._store._conn.execute(
                "SELECT id, file_path, symbol_name, chunk_type, content FROM chunks"
            )
        all_chunks = cur.fetchall()

        # Skip chunks already embedded
        existing_ids = self._vector_store.get_chunk_ids()
        all_chunks = [c for c in all_chunks if c["id"] not in existing_ids]
        total = len(all_chunks)
        if existing_ids:
            print(f"  Skipping {len(existing_ids)} already-embedded chunks")

        if total == 0:
            print("No chunks found in SQLite. Run tree-sitter parsing first.")
            return 0

        print(f"Embedding {total} chunks (batch size {self._batch_size})...")
        embedded = 0
        consecutive_errors = 0

        for batch_start in range(0, total, self._batch_size):
            batch = all_chunks[batch_start : batch_start + self._batch_size]
            batch_num = batch_start // self._batch_size + 1
            total_batches = (total + self._batch_size - 1) // self._batch_size

            texts = [row["content"] for row in batch]
            chunk_ids = [row["id"] for row in batch]
            file_paths = [row["file_path"] for row in batch]
            symbol_names = [row["symbol_name"] for row in batch]
            chunk_types = [row["chunk_type"] for row in batch]

            try:
                vectors = self._provider.embed(texts)
            except Exception as e:
                err_str = str(e)
                # Fail fast on authentication errors
                if "API_KEY_INVALID" in err_str or "PERMISSION_DENIED" in err_str:
                    raise RuntimeError(f"API key error — aborting: {e}") from e
                print(
                    f"  Error embedding batch {batch_num}/{total_batches}: {e}",
                    file=sys.stderr,
                )
                time.sleep(2)
                try:
                    vectors = self._provider.embed(texts)
                except Exception as e2:
                    print(
                        f"  Retry failed, skipping batch: {e2}",
                        file=sys.stderr,
                    )
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        raise RuntimeError(
                            f"3 consecutive batch failures — aborting. Last error: {e2}"
                        ) from e2
                    continue

            consecutive_errors = 0
            self._vector_store.add_chunks(
                vectors=vectors,
                chunk_ids=chunk_ids,
                file_paths=file_paths,
                symbol_names=symbol_names,
                chunk_types=chunk_types,
                contents=texts,
            )
            embedded += len(batch)

            if on_progress:
                on_progress({
                    "step": "embed", "current": embedded, "total": total,
                    "batch": batch_num, "total_batches": total_batches,
                })

            if batch_num % 10 == 0 or batch_num == total_batches:
                print(f"  Batch {batch_num}/{total_batches} — {embedded}/{total} chunks embedded")

        print(f"Embedding complete: {embedded} chunks embedded")
        return embedded
