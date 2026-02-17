"""Lexical (BM25) indexing using Tantivy."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import tantivy

from indiseek.storage.sqlite_store import SqliteStore

logger = logging.getLogger(__name__)

# Characters that Tantivy's query parser interprets as syntax
_TANTIVY_SPECIAL = re.compile(r'[+\-!(){}[\]^"~*?:\\/]')


class LexicalIndexer:
    """Builds and searches a Tantivy BM25 index over AST-scoped code chunks."""

    def __init__(self, store: SqliteStore, index_path: Path) -> None:
        self._store = store
        self._index_path = index_path
        self._index: tantivy.Index | None = None

    def _build_schema(self) -> tantivy.SchemaBuilder:
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("file_path", stored=True, tokenizer_name="raw")
        builder.add_text_field("content", stored=True, tokenizer_name="en_stem")
        builder.add_text_field("symbol_name", stored=True, tokenizer_name="raw")
        builder.add_text_field("chunk_type", stored=True, tokenizer_name="raw")
        builder.add_integer_field("chunk_id", stored=True, indexed=True)
        builder.add_integer_field("start_line", stored=True)
        builder.add_integer_field("end_line", stored=True)
        return builder

    def build_index(self) -> int:
        """Build the Tantivy index from chunks in SQLite.

        Recreates the index from scratch each time.
        Returns the number of documents indexed.
        """
        # Wipe and recreate index directory
        if self._index_path.exists():
            shutil.rmtree(self._index_path)
        self._index_path.mkdir(parents=True, exist_ok=True)

        schema = self._build_schema().build()
        index = tantivy.Index(schema, path=str(self._index_path))

        # Fetch all chunks from SQLite
        cur = self._store._conn.execute(
            "SELECT id, file_path, symbol_name, chunk_type, start_line, end_line, content "
            "FROM chunks"
        )
        rows = cur.fetchall()

        if not rows:
            self._index = index
            return 0

        writer = index.writer(heap_size=50_000_000)
        count = 0

        for row in rows:
            writer.add_document(tantivy.Document(
                chunk_id=row[0],
                file_path=[row[1]],
                symbol_name=[row[2] or ""],
                chunk_type=[row[3]],
                start_line=row[4],
                end_line=row[5],
                content=[row[6]],
            ))
            count += 1

        writer.commit()
        writer.wait_merging_threads()
        index.reload()

        self._index = index
        return count

    def open_index(self) -> None:
        """Open an existing Tantivy index from disk."""
        if not self._index_path.exists():
            raise FileNotFoundError(f"No index found at {self._index_path}")
        schema = self._build_schema().build()
        self._index = tantivy.Index(schema, path=str(self._index_path))
        self._index.reload()

    def _get_index(self) -> tantivy.Index:
        if self._index is None:
            self.open_index()
        return self._index  # type: ignore[return-value]

    def doc_count(self) -> int:
        """Return the number of documents in the index, or 0 if not open."""
        try:
            index = self._get_index()
            return index.searcher().num_docs
        except Exception:
            return 0

    def get_indexed_file_paths(self) -> set[str]:
        """Return distinct file paths in the index.

        Since Tantivy is always rebuilt from SQLite chunks, this is equivalent
        to the set of file paths in the chunks table. We query the index directly
        to confirm what's actually indexed.
        """
        try:
            index = self._get_index()
            searcher = index.searcher()
            # Search for all docs by matching everything
            query = index.parse_query("*", ["content"])
            results = searcher.search(query, limit=searcher.num_docs or 1)
            paths = set()
            for _score, doc_address in results.hits:
                doc = searcher.doc(doc_address)
                paths.add(doc["file_path"][0])
            return paths
        except Exception:
            return set()

    def search(self, query_str: str, limit: int = 10) -> list[LexicalResult]:
        """Search the BM25 index. Returns results sorted by relevance."""
        index = self._get_index()
        searcher = index.searcher()
        # Strip Tantivy query-parser special chars to avoid syntax errors
        sanitized = _TANTIVY_SPECIAL.sub(" ", query_str).strip()
        if not sanitized:
            return []
        query = index.parse_query(sanitized, ["content"])
        results = searcher.search(query, limit=limit)

        out = []
        for score, doc_address in results.hits:
            doc = searcher.doc(doc_address)
            out.append(LexicalResult(
                chunk_id=doc["chunk_id"][0],
                file_path=doc["file_path"][0],
                symbol_name=doc["symbol_name"][0] or None,
                chunk_type=doc["chunk_type"][0],
                content=doc["content"][0],
                start_line=doc["start_line"][0],
                end_line=doc["end_line"][0],
                score=score,
            ))
        return out


class LexicalResult:
    """Result from a lexical (BM25) search."""

    __slots__ = (
        "chunk_id", "file_path", "symbol_name", "chunk_type",
        "content", "start_line", "end_line", "score",
    )

    def __init__(
        self,
        chunk_id: int,
        file_path: str,
        symbol_name: str | None,
        chunk_type: str,
        content: str,
        start_line: int,
        end_line: int,
        score: float,
    ) -> None:
        self.chunk_id = chunk_id
        self.file_path = file_path
        self.symbol_name = symbol_name
        self.chunk_type = chunk_type
        self.content = content
        self.start_line = start_line
        self.end_line = end_line
        self.score = score
