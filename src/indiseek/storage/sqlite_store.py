"""SQLite storage for symbols, chunks, SCIP data, and file summaries."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Symbol:
    id: int | None
    file_path: str
    name: str
    kind: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int
    signature: str | None = None
    parent_symbol_id: int | None = None


@dataclass
class Chunk:
    id: int | None
    file_path: str
    symbol_name: str | None
    chunk_type: str
    start_line: int
    end_line: int
    content: str
    token_estimate: int | None = None


class SqliteStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def init_db(self) -> None:
        """Create all tables and indexes."""
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY,
                file_path TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                start_col INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                end_col INTEGER NOT NULL,
                signature TEXT,
                parent_symbol_id INTEGER,
                FOREIGN KEY (parent_symbol_id) REFERENCES symbols(id)
            );
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
            CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);
            CREATE INDEX IF NOT EXISTS idx_symbols_kind ON symbols(kind);

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                file_path TEXT NOT NULL,
                symbol_name TEXT,
                chunk_type TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                content TEXT NOT NULL,
                token_estimate INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_path);

            CREATE TABLE IF NOT EXISTS scip_symbols (
                id INTEGER PRIMARY KEY,
                symbol TEXT NOT NULL UNIQUE,
                documentation TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_scip_symbol ON scip_symbols(symbol);

            CREATE TABLE IF NOT EXISTS scip_occurrences (
                id INTEGER PRIMARY KEY,
                symbol_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                start_col INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                end_col INTEGER NOT NULL,
                role TEXT NOT NULL,
                FOREIGN KEY (symbol_id) REFERENCES scip_symbols(id)
            );
            CREATE INDEX IF NOT EXISTS idx_scip_occ_symbol ON scip_occurrences(symbol_id);
            CREATE INDEX IF NOT EXISTS idx_scip_occ_file ON scip_occurrences(file_path);

            CREATE TABLE IF NOT EXISTS scip_relationships (
                id INTEGER PRIMARY KEY,
                symbol_id INTEGER NOT NULL,
                related_symbol_id INTEGER NOT NULL,
                relationship TEXT NOT NULL,
                FOREIGN KEY (symbol_id) REFERENCES scip_symbols(id),
                FOREIGN KEY (related_symbol_id) REFERENCES scip_symbols(id)
            );

            CREATE TABLE IF NOT EXISTS file_summaries (
                id INTEGER PRIMARY KEY,
                file_path TEXT NOT NULL UNIQUE,
                summary TEXT NOT NULL,
                language TEXT,
                line_count INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_summaries_path ON file_summaries(file_path);

            CREATE TABLE IF NOT EXISTS queries (
                id INTEGER PRIMARY KEY,
                prompt TEXT NOT NULL,
                answer TEXT,
                evidence TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                error TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                duration_secs REAL,
                source_query_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self._conn.commit()
        # Migrate: add source_query_id to existing queries table
        cur.execute("PRAGMA table_info(queries)")
        columns = {row[1] for row in cur.fetchall()}
        if "source_query_id" not in columns:
            cur.execute("ALTER TABLE queries ADD COLUMN source_query_id INTEGER")
            self._conn.commit()

    def clear_index_data(self) -> None:
        """Delete all indexed data (symbols, chunks, SCIP) for a clean re-index."""
        self._conn.executescript(
            """
            DELETE FROM scip_relationships;
            DELETE FROM scip_occurrences;
            DELETE FROM scip_symbols;
            DELETE FROM chunks;
            DELETE FROM symbols;
            """
        )
        self._conn.commit()

    # ── Symbol operations ──

    def insert_symbols(self, symbols: list[Symbol]) -> None:
        """Batch insert symbols."""
        self._conn.executemany(
            """INSERT INTO symbols
               (file_path, name, kind, start_line, start_col, end_line, end_col, signature, parent_symbol_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    s.file_path, s.name, s.kind,
                    s.start_line, s.start_col, s.end_line, s.end_col,
                    s.signature, s.parent_symbol_id,
                )
                for s in symbols
            ],
        )
        self._conn.commit()

    def insert_symbol(self, symbol: Symbol) -> int:
        """Insert a single symbol and return its id."""
        cur = self._conn.execute(
            """INSERT INTO symbols
               (file_path, name, kind, start_line, start_col, end_line, end_col, signature, parent_symbol_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol.file_path, symbol.name, symbol.kind,
                symbol.start_line, symbol.start_col, symbol.end_line, symbol.end_col,
                symbol.signature, symbol.parent_symbol_id,
            ),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_symbols_by_name(self, name: str) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM symbols WHERE name = ?", (name,))
        return [dict(row) for row in cur.fetchall()]

    def get_symbols_by_file(self, file_path: str) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM symbols WHERE file_path = ?", (file_path,))
        return [dict(row) for row in cur.fetchall()]

    def get_symbols_in_range(self, file_path: str, start_line: int, end_line: int) -> list[dict]:
        """Find symbols whose definition starts within the given line range."""
        cur = self._conn.execute(
            """SELECT * FROM symbols 
               WHERE file_path = ? AND start_line >= ? AND start_line <= ?
               ORDER BY start_line""",
            (file_path, start_line, end_line),
        )
        return [dict(row) for row in cur.fetchall()]

    # ── Chunk operations ──

    def insert_chunks(self, chunks: list[Chunk]) -> None:
        """Batch insert chunks."""
        self._conn.executemany(
            """INSERT INTO chunks
               (file_path, symbol_name, chunk_type, start_line, end_line, content, token_estimate)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    c.file_path, c.symbol_name, c.chunk_type,
                    c.start_line, c.end_line, c.content, c.token_estimate,
                )
                for c in chunks
            ],
        )
        self._conn.commit()

    def get_chunks_by_file(self, file_path: str) -> list[dict]:
        cur = self._conn.execute("SELECT * FROM chunks WHERE file_path = ?", (file_path,))
        return [dict(row) for row in cur.fetchall()]

    # ── SCIP operations ──

    def insert_scip_symbol(self, symbol: str, documentation: str | None = None) -> int:
        """Insert a SCIP symbol and return its id. Returns existing id if duplicate."""
        cur = self._conn.execute(
            "SELECT id FROM scip_symbols WHERE symbol = ?", (symbol,)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur = self._conn.execute(
            "INSERT INTO scip_symbols (symbol, documentation) VALUES (?, ?)",
            (symbol, documentation),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def insert_scip_occurrences(
        self, occurrences: list[tuple[int, str, int, int, int, int, str]]
    ) -> None:
        """Batch insert SCIP occurrences.

        Each tuple: (symbol_id, file_path, start_line, start_col, end_line, end_col, role)
        """
        self._conn.executemany(
            """INSERT INTO scip_occurrences
               (symbol_id, file_path, start_line, start_col, end_line, end_col, role)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            occurrences,
        )
        self._conn.commit()

    def insert_scip_relationship(
        self, symbol_id: int, related_symbol_id: int, relationship: str
    ) -> None:
        """Insert a SCIP relationship between two symbols."""
        self._conn.execute(
            """INSERT INTO scip_relationships (symbol_id, related_symbol_id, relationship)
               VALUES (?, ?, ?)""",
            (symbol_id, related_symbol_id, relationship),
        )
        self._conn.commit()

    def get_scip_symbol_id(self, symbol: str) -> int | None:
        """Look up a SCIP symbol id by its string identifier."""
        cur = self._conn.execute(
            "SELECT id FROM scip_symbols WHERE symbol = ?", (symbol,)
        )
        row = cur.fetchone()
        return row[0] if row else None

    def get_definition(self, symbol_name: str) -> list[dict]:
        """Find definition locations for a symbol by name substring match."""
        cur = self._conn.execute(
            """SELECT ss.symbol, so.file_path, so.start_line, so.start_col,
                      so.end_line, so.end_col
               FROM scip_occurrences so
               JOIN scip_symbols ss ON so.symbol_id = ss.id
               WHERE so.role = 'definition' AND ss.symbol LIKE '%' || ? || '%'""",
            (symbol_name,),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_references(self, symbol_name: str) -> list[dict]:
        """Find all reference locations for a symbol by name substring match."""
        cur = self._conn.execute(
            """SELECT ss.symbol, so.file_path, so.start_line, so.start_col,
                      so.end_line, so.end_col, so.role
               FROM scip_occurrences so
               JOIN scip_symbols ss ON so.symbol_id = ss.id
               WHERE so.role = 'reference' AND ss.symbol LIKE '%' || ? || '%'""",
            (symbol_name,),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_scip_occurrences_by_symbol_id(self, symbol_id: int) -> list[dict]:
        """Get all occurrences for a specific SCIP symbol id."""
        cur = self._conn.execute(
            """SELECT file_path, start_line, start_col, end_line, end_col, role
               FROM scip_occurrences WHERE symbol_id = ?""",
            (symbol_id,),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_scip_relationships_for(self, symbol_id: int) -> list[dict]:
        """Get relationships where this symbol is the subject."""
        cur = self._conn.execute(
            """SELECT sr.relationship, ss.symbol AS related_symbol
               FROM scip_relationships sr
               JOIN scip_symbols ss ON sr.related_symbol_id = ss.id
               WHERE sr.symbol_id = ?""",
            (symbol_id,),
        )
        return [dict(row) for row in cur.fetchall()]

    # ── File summary operations ──

    def insert_file_summary(
        self, file_path: str, summary: str, language: str | None, line_count: int | None
    ) -> None:
        """Insert or replace a file summary."""
        self._conn.execute(
            """INSERT OR REPLACE INTO file_summaries (file_path, summary, language, line_count)
               VALUES (?, ?, ?, ?)""",
            (file_path, summary, language, line_count),
        )
        self._conn.commit()

    def insert_file_summaries(
        self, summaries: list[tuple[str, str, str | None, int | None]]
    ) -> None:
        """Batch insert file summaries. Each tuple: (file_path, summary, language, line_count)."""
        self._conn.executemany(
            """INSERT OR REPLACE INTO file_summaries (file_path, summary, language, line_count)
               VALUES (?, ?, ?, ?)""",
            summaries,
        )
        self._conn.commit()

    def get_file_summaries(self, directory: str | None = None) -> list[dict]:
        """Get file summaries, optionally scoped to a subdirectory."""
        if directory:
            # Ensure directory ends with / for prefix matching
            prefix = directory.rstrip("/") + "/"
            cur = self._conn.execute(
                "SELECT * FROM file_summaries WHERE file_path LIKE ? ORDER BY file_path",
                (prefix + "%",),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM file_summaries ORDER BY file_path"
            )
        return [dict(row) for row in cur.fetchall()]

    def get_directory_tree(self) -> dict:
        """Return nested dict of {dir: {file: summary, subdir: {...}}}."""
        summaries = self.get_file_summaries()
        tree: dict = {}
        for row in summaries:
            parts = row["file_path"].split("/")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = row["summary"]
        return tree

    # ── Dashboard query methods ──

    def get_chunk_by_id(self, chunk_id: int) -> dict | None:
        """Look up a single chunk by primary key."""
        cur = self._conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_all_file_paths_from_chunks(self) -> set[str]:
        """Return distinct file paths that have chunks."""
        cur = self._conn.execute("SELECT DISTINCT file_path FROM chunks")
        return {row[0] for row in cur.fetchall()}

    def get_all_file_paths_from_summaries(self) -> set[str]:
        """Return distinct file paths that have summaries."""
        cur = self._conn.execute("SELECT DISTINCT file_path FROM file_summaries")
        return {row[0] for row in cur.fetchall()}

    def get_file_summary(self, file_path: str) -> dict | None:
        """Look up a single file summary by exact path."""
        cur = self._conn.execute(
            "SELECT * FROM file_summaries WHERE file_path = ?", (file_path,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def clear_index_data_for_prefix(self, prefix: str) -> dict[str, int]:
        """Delete chunks and symbols for files matching a path prefix.

        Does NOT touch SCIP data or file_summaries.
        Returns counts of deleted rows.
        """
        pattern = prefix + "%"
        cur_chunks = self._conn.execute(
            "DELETE FROM chunks WHERE file_path LIKE ?", (pattern,)
        )
        cur_symbols = self._conn.execute(
            "DELETE FROM symbols WHERE file_path LIKE ?", (pattern,)
        )
        self._conn.commit()
        return {
            "chunks_deleted": cur_chunks.rowcount,
            "symbols_deleted": cur_symbols.rowcount,
        }

    # ── Metadata ──

    def set_metadata(self, key: str, value: str) -> None:
        """Set a metadata key-value pair (INSERT OR REPLACE)."""
        self._conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    def get_metadata(self, key: str) -> str | None:
        """Get a metadata value by key, or None if not found."""
        cur = self._conn.execute("SELECT value FROM metadata WHERE key = ?", (key,))
        row = cur.fetchone()
        return row[0] if row else None

    # ── Query history ──

    def insert_query(self, prompt: str) -> int:
        """Insert a new query with status='running'. Returns its id."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO queries (prompt, status, created_at) VALUES (?, 'running', ?)",
            (prompt, now),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def complete_query(
        self, query_id: int, answer: str, evidence_json: str, duration_secs: float
    ) -> None:
        """Mark a query as completed with its answer and evidence."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE queries
               SET answer = ?, evidence = ?, status = 'completed',
                   completed_at = ?, duration_secs = ?
               WHERE id = ?""",
            (answer, evidence_json, now, duration_secs, query_id),
        )
        self._conn.commit()

    def fail_query(self, query_id: int, error: str) -> None:
        """Mark a query as failed."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE queries SET status = 'failed', error = ?, completed_at = ? WHERE id = ?",
            (error, now, query_id),
        )
        self._conn.commit()

    def list_queries(self, limit: int = 50) -> list[dict]:
        """List recent queries (without answer/evidence for efficiency)."""
        cur = self._conn.execute(
            """SELECT id, prompt, status, created_at, duration_secs
               FROM queries ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_completed_queries_since(self, after: str | None = None) -> list[dict]:
        """Return completed queries, optionally only those completed after a timestamp."""
        if after:
            cur = self._conn.execute(
                """SELECT id, prompt, answer, evidence, duration_secs
                   FROM queries WHERE status = 'completed' AND completed_at > ?
                   ORDER BY completed_at DESC""",
                (after,),
            )
        else:
            cur = self._conn.execute(
                """SELECT id, prompt, answer, evidence, duration_secs
                   FROM queries WHERE status = 'completed'
                   ORDER BY completed_at DESC""",
            )
        return [dict(row) for row in cur.fetchall()]

    def insert_cached_query(
        self, prompt: str, answer: str, evidence_json: str,
        source_query_id: int, duration_secs: float,
    ) -> int:
        """Insert a cached query entry pointing to its source."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            """INSERT INTO queries
               (prompt, answer, evidence, status, created_at, completed_at,
                duration_secs, source_query_id)
               VALUES (?, ?, ?, 'cached', ?, ?, ?, ?)""",
            (prompt, answer, evidence_json, now, now, duration_secs, source_query_id),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_query(self, query_id: int) -> dict | None:
        """Get full query details including answer and evidence."""
        cur = self._conn.execute("SELECT * FROM queries WHERE id = ?", (query_id,))
        row = cur.fetchone()
        if not row:
            return None
        result = dict(row)
        # Parse evidence JSON back to list
        if result.get("evidence"):
            try:
                result["evidence"] = json.loads(result["evidence"])
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    # ── Counts ──

    def count(self, table: str) -> int:
        cur = self._conn.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
        return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()
