"""Vector storage using LanceDB for semantic search."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import lancedb
import pyarrow as pa


@dataclass
class SearchResult:
    chunk_id: int
    file_path: str
    symbol_name: str | None
    chunk_type: str
    content: str
    score: float


class VectorStore:
    """LanceDB vector store for code chunk embeddings."""

    TABLE_NAME = "chunks"

    def __init__(self, db_path: Path, dims: int = 768) -> None:
        self._db_path = db_path
        self._dims = dims
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(db_path))
        self._table: lancedb.table.Table | None = None

    def _schema(self) -> pa.Schema:
        return pa.schema(
            [
                pa.field("vector", pa.list_(pa.float32(), self._dims)),
                pa.field("chunk_id", pa.int64()),
                pa.field("file_path", pa.utf8()),
                pa.field("symbol_name", pa.utf8()),
                pa.field("chunk_type", pa.utf8()),
                pa.field("content", pa.utf8()),
            ]
        )

    def init_table(self) -> None:
        """Create the chunks table if it doesn't exist, or open it."""
        existing = self._db.list_tables().tables
        if self.TABLE_NAME in existing:
            self._table = self._db.open_table(self.TABLE_NAME)
        else:
            self._table = self._db.create_table(
                self.TABLE_NAME, schema=self._schema()
            )

    def _get_table(self) -> lancedb.table.Table:
        if self._table is None:
            self.init_table()
        return self._table  # type: ignore[return-value]

    def add_chunks(
        self,
        vectors: list[list[float]],
        chunk_ids: list[int],
        file_paths: list[str],
        symbol_names: list[str | None],
        chunk_types: list[str],
        contents: list[str],
    ) -> None:
        """Batch insert chunks with their embedding vectors."""
        rows = [
            {
                "vector": vec,
                "chunk_id": cid,
                "file_path": fp,
                "symbol_name": sn or "",
                "chunk_type": ct,
                "content": content,
            }
            for vec, cid, fp, sn, ct, content in zip(
                vectors, chunk_ids, file_paths, symbol_names, chunk_types, contents
            )
        ]
        self._get_table().add(rows)

    def search(self, query_vector: list[float], limit: int = 10) -> list[SearchResult]:
        """Search for similar chunks by cosine distance.

        Returns results sorted by relevance (lowest distance = most similar).
        """
        results = (
            self._get_table()
            .search(query_vector)
            .distance_type("cosine")
            .limit(limit)
            .to_list()
        )
        return [
            SearchResult(
                chunk_id=int(r["chunk_id"]),
                file_path=r["file_path"],
                symbol_name=r["symbol_name"] or None,
                chunk_type=r["chunk_type"],
                content=r["content"],
                score=float(r["_distance"]),
            )
            for r in results
        ]

    def count(self) -> int:
        """Return the number of rows in the table."""
        return self._get_table().count_rows()
