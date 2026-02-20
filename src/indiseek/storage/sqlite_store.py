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

            CREATE TABLE IF NOT EXISTS file_contents (
                file_path TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                line_count INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS directory_summaries (
                id INTEGER PRIMARY KEY,
                dir_path TEXT UNIQUE,
                summary TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS repos (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                url TEXT,
                local_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_indexed_at TEXT,
                indexed_commit_sha TEXT,
                current_commit_sha TEXT,
                commits_behind INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE INDEX IF NOT EXISTS idx_repos_name ON repos(name);

            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self._conn.commit()

        # ── Migrations ──
        # Pattern: check if column exists, ALTER if missing.

        # Migrate: add source_query_id to existing queries table
        self._migrate_add_column("queries", "source_query_id", "INTEGER")

        # Migrate: add repo_id to all data tables (default 1 = legacy repo)
        for table in [
            "symbols", "chunks", "file_summaries",
            "scip_symbols", "scip_occurrences", "scip_relationships",
            "queries", "file_contents", "directory_summaries",
        ]:
            self._migrate_add_column(table, "repo_id", "INTEGER DEFAULT 1")

        # Migrate: add cost tracking columns to queries
        self._migrate_add_column("queries", "prompt_tokens", "INTEGER")
        self._migrate_add_column("queries", "completion_tokens", "INTEGER")
        self._migrate_add_column("queries", "estimated_cost", "REAL")

        # Create indexes for repo_id columns
        for table in [
            "symbols", "chunks", "file_summaries",
            "scip_symbols", "scip_occurrences", "scip_relationships",
            "queries", "file_contents", "directory_summaries",
        ]:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_repo_id ON {table}(repo_id)"
            )
        self._conn.commit()

        # Migrate: fix unique constraints to be composite (col + repo_id)
        # instead of just col. Required for multi-repo data isolation.
        self._migrate_composite_unique("file_contents", "file_path", [
            "file_path TEXT NOT NULL",
            "content TEXT NOT NULL",
            "line_count INTEGER NOT NULL",
            "repo_id INTEGER DEFAULT 1",
        ], "UNIQUE(file_path, repo_id)")
        self._migrate_composite_unique("directory_summaries", "dir_path", [
            "id INTEGER PRIMARY KEY",
            "dir_path TEXT NOT NULL",
            "summary TEXT NOT NULL",
            "repo_id INTEGER DEFAULT 1",
        ], "UNIQUE(dir_path, repo_id)")
        self._migrate_composite_unique("file_summaries", "file_path", [
            "id INTEGER PRIMARY KEY",
            "file_path TEXT NOT NULL",
            "summary TEXT NOT NULL",
            "language TEXT",
            "line_count INTEGER",
            "repo_id INTEGER DEFAULT 1",
        ], "UNIQUE(file_path, repo_id)")
        self._migrate_composite_unique("scip_symbols", "symbol", [
            "id INTEGER PRIMARY KEY",
            "symbol TEXT NOT NULL",
            "documentation TEXT",
            "repo_id INTEGER DEFAULT 1",
        ], "UNIQUE(symbol, repo_id)")

        # Auto-create legacy repo row if data exists but repos is empty
        self._ensure_legacy_repo()

    def _migrate_add_column(self, table: str, column: str, col_type: str) -> None:
        """Add a column to a table if it doesn't exist."""
        cur = self._conn.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cur.fetchall()}
        if column not in columns:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            self._conn.commit()

    def _migrate_composite_unique(
        self, table: str, unique_col: str, column_defs: list[str], constraint: str,
    ) -> None:
        """Rebuild a table to change its UNIQUE constraint to a composite one.

        Only runs if the table's current DDL lacks the composite constraint.
        Preserves all existing data via copy.
        """
        cur = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        row = cur.fetchone()
        if not row:
            return
        ddl = row[0]
        # Skip if constraint already present
        if constraint.replace(" ", "") in ddl.replace(" ", ""):
            return
        tmp = f"_{table}_migrate"
        cols = ", ".join(c.split()[0] for c in column_defs)
        self._conn.executescript(f"""
            CREATE TABLE {tmp} ({', '.join(column_defs)}, {constraint});
            INSERT INTO {tmp} ({cols}) SELECT {cols} FROM {table};
            DROP TABLE {table};
            ALTER TABLE {tmp} RENAME TO {table};
            CREATE INDEX IF NOT EXISTS idx_{table}_repo_id ON {table}(repo_id);
        """)
        self._conn.commit()

    def _ensure_legacy_repo(self) -> None:
        """Auto-create legacy repo row (id=1) if data exists but repos table is empty."""
        cur = self._conn.execute("SELECT COUNT(*) FROM repos")
        if cur.fetchone()[0] > 0:
            # Backfill: if legacy repo exists but indexed_commit_sha is null and data exists
            self._backfill_legacy_sha()
            return
        # Check if there's any indexed data
        cur = self._conn.execute("SELECT COUNT(*) FROM symbols")
        has_data = cur.fetchone()[0] > 0
        if not has_data:
            return
        # Derive repo name from REPO_PATH env var if available
        import os
        repo_path = os.getenv("REPO_PATH", "")
        name = Path(repo_path).name if repo_path else "legacy"
        local_path = repo_path or "."
        now = datetime.now(timezone.utc).isoformat()
        # If repo exists on disk, grab the HEAD SHA so /check knows it's been indexed
        head_sha = None
        if repo_path and Path(repo_path).is_dir():
            try:
                import subprocess
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True, text=True, cwd=repo_path,
                )
                if result.returncode == 0:
                    head_sha = result.stdout.strip()
            except Exception:
                pass
        self._conn.execute(
            """INSERT INTO repos (id, name, url, local_path, created_at, status,
                                  indexed_commit_sha, last_indexed_at)
               VALUES (1, ?, NULL, ?, ?, 'active', ?, ?)""",
            (name, local_path, now, head_sha, now if head_sha else None),
        )
        self._conn.commit()

    def _backfill_legacy_sha(self) -> None:
        """Backfill indexed_commit_sha for legacy repo if it has data but no SHA."""
        cur = self._conn.execute(
            "SELECT local_path, indexed_commit_sha FROM repos WHERE id = 1"
        )
        row = cur.fetchone()
        if not row or row[1] is not None:
            return
        local_path = row[0]
        if not local_path or not Path(local_path).is_dir():
            return
        try:
            import subprocess
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=local_path,
            )
            if result.returncode == 0:
                sha = result.stdout.strip()
                now = datetime.now(timezone.utc).isoformat()
                self._conn.execute(
                    "UPDATE repos SET indexed_commit_sha = ?, last_indexed_at = ? WHERE id = 1",
                    (sha, now),
                )
                self._conn.commit()
        except Exception:
            pass

    # ── Repo operations ──

    def insert_repo(
        self, name: str, local_path: str, url: str | None = None, status: str = "active"
    ) -> int:
        """Insert a new repo and return its id."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            """INSERT INTO repos (name, url, local_path, created_at, status)
               VALUES (?, ?, ?, ?, ?)""",
            (name, url, local_path, now, status),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_repo(self, repo_id: int) -> dict | None:
        """Get a repo by id."""
        cur = self._conn.execute("SELECT * FROM repos WHERE id = ?", (repo_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_repo_by_name(self, name: str) -> dict | None:
        """Get a repo by name."""
        cur = self._conn.execute("SELECT * FROM repos WHERE name = ?", (name,))
        row = cur.fetchone()
        return dict(row) if row else None

    def list_repos(self) -> list[dict]:
        """List all repos ordered by name."""
        cur = self._conn.execute("SELECT * FROM repos ORDER BY name")
        return [dict(row) for row in cur.fetchall()]

    def update_repo(self, repo_id: int, **kwargs: str | int | None) -> None:
        """Update repo fields. Pass column=value keyword arguments."""
        if not kwargs:
            return
        set_clause = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [repo_id]
        self._conn.execute(
            f"UPDATE repos SET {set_clause} WHERE id = ?", values  # noqa: S608
        )
        self._conn.commit()

    def delete_repo(self, repo_id: int) -> None:
        """Delete a repo by id."""
        self._conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
        self._conn.commit()

    def clear_index_data(self, repo_id: int = 1) -> None:
        """Delete all indexed data (symbols, chunks, SCIP, file contents) for a clean re-index."""
        for table in [
            "scip_relationships", "scip_occurrences", "scip_symbols",
            "chunks", "symbols", "file_contents",
        ]:
            self._conn.execute(f"DELETE FROM {table} WHERE repo_id = ?", (repo_id,))
        self._conn.commit()

    # ── Symbol operations ──

    def insert_symbols(self, symbols: list[Symbol], repo_id: int = 1) -> None:
        """Batch insert symbols."""
        self._conn.executemany(
            """INSERT INTO symbols
               (file_path, name, kind, start_line, start_col, end_line, end_col, signature, parent_symbol_id, repo_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    s.file_path, s.name, s.kind,
                    s.start_line, s.start_col, s.end_line, s.end_col,
                    s.signature, s.parent_symbol_id, repo_id,
                )
                for s in symbols
            ],
        )
        self._conn.commit()

    def insert_symbol(self, symbol: Symbol, repo_id: int = 1) -> int:
        """Insert a single symbol and return its id."""
        cur = self._conn.execute(
            """INSERT INTO symbols
               (file_path, name, kind, start_line, start_col, end_line, end_col, signature, parent_symbol_id, repo_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                symbol.file_path, symbol.name, symbol.kind,
                symbol.start_line, symbol.start_col, symbol.end_line, symbol.end_col,
                symbol.signature, symbol.parent_symbol_id, repo_id,
            ),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_symbols_by_name(self, name: str, repo_id: int = 1) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM symbols WHERE name = ? AND repo_id = ?", (name, repo_id)
        )
        return [dict(row) for row in cur.fetchall()]

    def get_symbols_by_file(self, file_path: str, repo_id: int = 1) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM symbols WHERE file_path = ? AND repo_id = ?", (file_path, repo_id)
        )
        return [dict(row) for row in cur.fetchall()]

    def get_symbols_in_range(
        self, file_path: str, start_line: int, end_line: int, repo_id: int = 1
    ) -> list[dict]:
        """Find symbols whose definition starts within the given line range."""
        cur = self._conn.execute(
            """SELECT * FROM symbols
               WHERE file_path = ? AND start_line >= ? AND start_line <= ? AND repo_id = ?
               ORDER BY start_line""",
            (file_path, start_line, end_line, repo_id),
        )
        return [dict(row) for row in cur.fetchall()]

    # ── Chunk operations ──

    def insert_chunks(self, chunks: list[Chunk], repo_id: int = 1) -> None:
        """Batch insert chunks."""
        self._conn.executemany(
            """INSERT INTO chunks
               (file_path, symbol_name, chunk_type, start_line, end_line, content, token_estimate, repo_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    c.file_path, c.symbol_name, c.chunk_type,
                    c.start_line, c.end_line, c.content, c.token_estimate, repo_id,
                )
                for c in chunks
            ],
        )
        self._conn.commit()

    def get_chunks_by_file(self, file_path: str, repo_id: int = 1) -> list[dict]:
        cur = self._conn.execute(
            "SELECT * FROM chunks WHERE file_path = ? AND repo_id = ?", (file_path, repo_id)
        )
        return [dict(row) for row in cur.fetchall()]

    # ── SCIP operations ──

    def insert_scip_symbol(
        self, symbol: str, documentation: str | None = None, repo_id: int = 1
    ) -> int:
        """Insert a SCIP symbol and return its id. Returns existing id if duplicate."""
        cur = self._conn.execute(
            "SELECT id FROM scip_symbols WHERE symbol = ? AND repo_id = ?", (symbol, repo_id)
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur = self._conn.execute(
            "INSERT INTO scip_symbols (symbol, documentation, repo_id) VALUES (?, ?, ?)",
            (symbol, documentation, repo_id),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def insert_scip_occurrences(
        self, occurrences: list[tuple[int, str, int, int, int, int, str]],
        repo_id: int = 1,
    ) -> None:
        """Batch insert SCIP occurrences.

        Each tuple: (symbol_id, file_path, start_line, start_col, end_line, end_col, role)
        """
        self._conn.executemany(
            """INSERT INTO scip_occurrences
               (symbol_id, file_path, start_line, start_col, end_line, end_col, role, repo_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(*occ, repo_id) for occ in occurrences],
        )
        self._conn.commit()

    def insert_scip_relationship(
        self, symbol_id: int, related_symbol_id: int, relationship: str, repo_id: int = 1
    ) -> None:
        """Insert a SCIP relationship between two symbols."""
        self._conn.execute(
            """INSERT INTO scip_relationships (symbol_id, related_symbol_id, relationship, repo_id)
               VALUES (?, ?, ?, ?)""",
            (symbol_id, related_symbol_id, relationship, repo_id),
        )
        self._conn.commit()

    def get_scip_symbol_id(self, symbol: str, repo_id: int = 1) -> int | None:
        """Look up a SCIP symbol id by its string identifier."""
        cur = self._conn.execute(
            "SELECT id FROM scip_symbols WHERE symbol = ? AND repo_id = ?", (symbol, repo_id)
        )
        row = cur.fetchone()
        return row[0] if row else None

    def get_definition(self, symbol_name: str, repo_id: int = 1) -> list[dict]:
        """Find definition locations for a symbol by name substring match."""
        cur = self._conn.execute(
            """SELECT ss.symbol, so.file_path, so.start_line, so.start_col,
                      so.end_line, so.end_col
               FROM scip_occurrences so
               JOIN scip_symbols ss ON so.symbol_id = ss.id
               WHERE so.role = 'definition' AND ss.symbol LIKE '%' || ? || '%'
                     AND so.repo_id = ?""",
            (symbol_name, repo_id),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_references(self, symbol_name: str, repo_id: int = 1) -> list[dict]:
        """Find all reference locations for a symbol by name substring match."""
        cur = self._conn.execute(
            """SELECT ss.symbol, so.file_path, so.start_line, so.start_col,
                      so.end_line, so.end_col, so.role
               FROM scip_occurrences so
               JOIN scip_symbols ss ON so.symbol_id = ss.id
               WHERE so.role = 'reference' AND ss.symbol LIKE '%' || ? || '%'
                     AND so.repo_id = ?""",
            (symbol_name, repo_id),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_scip_occurrences_by_symbol_id(
        self, symbol_id: int, repo_id: int = 1
    ) -> list[dict]:
        """Get all occurrences for a specific SCIP symbol id."""
        cur = self._conn.execute(
            """SELECT file_path, start_line, start_col, end_line, end_col, role
               FROM scip_occurrences WHERE symbol_id = ? AND repo_id = ?""",
            (symbol_id, repo_id),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_scip_relationships_for(self, symbol_id: int, repo_id: int = 1) -> list[dict]:
        """Get relationships where this symbol is the subject."""
        cur = self._conn.execute(
            """SELECT sr.relationship, ss.symbol AS related_symbol
               FROM scip_relationships sr
               JOIN scip_symbols ss ON sr.related_symbol_id = ss.id
               WHERE sr.symbol_id = ? AND sr.repo_id = ?""",
            (symbol_id, repo_id),
        )
        return [dict(row) for row in cur.fetchall()]

    # ── File summary operations ──

    def insert_file_summary(
        self, file_path: str, summary: str, language: str | None, line_count: int | None,
        repo_id: int = 1,
    ) -> None:
        """Insert or replace a file summary."""
        self._conn.execute(
            """INSERT OR REPLACE INTO file_summaries (file_path, summary, language, line_count, repo_id)
               VALUES (?, ?, ?, ?, ?)""",
            (file_path, summary, language, line_count, repo_id),
        )
        self._conn.commit()

    def insert_file_summaries(
        self, summaries: list[tuple[str, str, str | None, int | None]],
        repo_id: int = 1,
    ) -> None:
        """Batch insert file summaries. Each tuple: (file_path, summary, language, line_count)."""
        self._conn.executemany(
            """INSERT OR REPLACE INTO file_summaries (file_path, summary, language, line_count, repo_id)
               VALUES (?, ?, ?, ?, ?)""",
            [(*s, repo_id) for s in summaries],
        )
        self._conn.commit()

    def get_file_summaries(
        self, directory: str | None = None, repo_id: int = 1
    ) -> list[dict]:
        """Get file summaries, optionally scoped to a subdirectory."""
        if directory:
            # Ensure directory ends with / for prefix matching
            prefix = directory.rstrip("/") + "/"
            cur = self._conn.execute(
                "SELECT * FROM file_summaries WHERE file_path LIKE ? AND repo_id = ? ORDER BY file_path",
                (prefix + "%", repo_id),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM file_summaries WHERE repo_id = ? ORDER BY file_path",
                (repo_id,),
            )
        return [dict(row) for row in cur.fetchall()]

    def get_directory_tree(self, repo_id: int = 1) -> dict:
        """Return nested dict of {dir: {file: summary, subdir: {...}}}."""
        summaries = self.get_file_summaries(repo_id=repo_id)
        tree: dict = {}
        for row in summaries:
            parts = row["file_path"].split("/")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = row["summary"]
        return tree

    # ── Directory summary operations ──

    def insert_directory_summary(self, dir_path: str, summary: str, repo_id: int = 1) -> None:
        """Insert or replace a directory summary."""
        self._conn.execute(
            "INSERT OR REPLACE INTO directory_summaries (dir_path, summary, repo_id) VALUES (?, ?, ?)",
            (dir_path, summary, repo_id),
        )
        self._conn.commit()

    def insert_directory_summaries(
        self, summaries: list[tuple[str, str]], repo_id: int = 1
    ) -> None:
        """Batch insert directory summaries. Each tuple: (dir_path, summary)."""
        self._conn.executemany(
            "INSERT OR REPLACE INTO directory_summaries (dir_path, summary, repo_id) VALUES (?, ?, ?)",
            [(*s, repo_id) for s in summaries],
        )
        self._conn.commit()

    def get_directory_summary(self, dir_path: str, repo_id: int = 1) -> dict | None:
        """Get a single directory summary by path."""
        cur = self._conn.execute(
            "SELECT * FROM directory_summaries WHERE dir_path = ? AND repo_id = ?",
            (dir_path, repo_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_directory_summaries(self, paths: list[str], repo_id: int = 1) -> dict[str, str]:
        """Batch lookup directory summaries. Returns {dir_path: summary}."""
        if not paths:
            return {}
        placeholders = ",".join("?" for _ in paths)
        cur = self._conn.execute(
            f"SELECT dir_path, summary FROM directory_summaries WHERE dir_path IN ({placeholders}) AND repo_id = ?",  # noqa: S608
            [*paths, repo_id],
        )
        return {row["dir_path"]: row["summary"] for row in cur.fetchall()}

    def get_all_directory_paths_from_summaries(self, repo_id: int = 1) -> set[str]:
        """Return all directory paths that have summaries."""
        cur = self._conn.execute(
            "SELECT dir_path FROM directory_summaries WHERE repo_id = ?", (repo_id,)
        )
        return {row[0] for row in cur.fetchall()}

    # ── File contents operations ──

    def insert_file_content(self, file_path: str, content: str, repo_id: int = 1) -> None:
        """Insert or replace a file's full content."""
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        self._conn.execute(
            "INSERT OR REPLACE INTO file_contents (file_path, content, line_count, repo_id) VALUES (?, ?, ?, ?)",
            (file_path, content, line_count, repo_id),
        )
        self._conn.commit()

    def get_file_content(self, file_path: str, repo_id: int = 1) -> str | None:
        """Get a file's content by path, or None if not stored."""
        cur = self._conn.execute(
            "SELECT content FROM file_contents WHERE file_path = ? AND repo_id = ?",
            (file_path, repo_id),
        )
        row = cur.fetchone()
        return row[0] if row else None

    # ── Dashboard query methods ──

    def get_chunk_by_id(self, chunk_id: int, repo_id: int = 1) -> dict | None:
        """Look up a single chunk by primary key."""
        cur = self._conn.execute(
            "SELECT * FROM chunks WHERE id = ? AND repo_id = ?", (chunk_id, repo_id)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def get_all_file_paths_from_chunks(self, repo_id: int = 1) -> set[str]:
        """Return distinct file paths that have chunks."""
        cur = self._conn.execute(
            "SELECT DISTINCT file_path FROM chunks WHERE repo_id = ?", (repo_id,)
        )
        return {row[0] for row in cur.fetchall()}

    def get_all_file_paths_from_summaries(self, repo_id: int = 1) -> set[str]:
        """Return distinct file paths that have summaries."""
        cur = self._conn.execute(
            "SELECT DISTINCT file_path FROM file_summaries WHERE repo_id = ?", (repo_id,)
        )
        return {row[0] for row in cur.fetchall()}

    def get_all_file_paths_from_file_contents(self, repo_id: int = 1) -> set[str]:
        """Return distinct file paths stored in file_contents."""
        cur = self._conn.execute(
            "SELECT DISTINCT file_path FROM file_contents WHERE repo_id = ?", (repo_id,)
        )
        return {row[0] for row in cur.fetchall()}

    def get_file_summary(self, file_path: str, repo_id: int = 1) -> dict | None:
        """Look up a single file summary by exact path."""
        cur = self._conn.execute(
            "SELECT * FROM file_summaries WHERE file_path = ? AND repo_id = ?",
            (file_path, repo_id),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def clear_index_data_for_prefix(self, prefix: str, repo_id: int = 1) -> dict[str, int]:
        """Delete chunks and symbols for files matching a path prefix.

        Does NOT touch SCIP data or file_summaries.
        Returns counts of deleted rows.
        """
        pattern = prefix + "%"
        cur_chunks = self._conn.execute(
            "DELETE FROM chunks WHERE file_path LIKE ? AND repo_id = ?", (pattern, repo_id)
        )
        cur_symbols = self._conn.execute(
            "DELETE FROM symbols WHERE file_path LIKE ? AND repo_id = ?", (pattern, repo_id)
        )
        self._conn.commit()
        return {
            "chunks_deleted": cur_chunks.rowcount,
            "symbols_deleted": cur_symbols.rowcount,
        }

    def delete_file_summaries_for_paths(self, file_paths: list[str], repo_id: int = 1) -> int:
        """Delete file summaries for the given exact paths. Returns count deleted."""
        if not file_paths:
            return 0
        placeholders = ",".join("?" for _ in file_paths)
        cur = self._conn.execute(
            f"DELETE FROM file_summaries WHERE file_path IN ({placeholders}) AND repo_id = ?",
            [*file_paths, repo_id],
        )
        self._conn.commit()
        return cur.rowcount

    def delete_directory_summaries_for_paths(self, dir_paths: list[str], repo_id: int = 1) -> int:
        """Delete directory summaries for the given exact paths. Returns count deleted."""
        if not dir_paths:
            return 0
        placeholders = ",".join("?" for _ in dir_paths)
        cur = self._conn.execute(
            f"DELETE FROM directory_summaries WHERE dir_path IN ({placeholders}) AND repo_id = ?",
            [*dir_paths, repo_id],
        )
        self._conn.commit()
        return cur.rowcount

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

    def insert_query(self, prompt: str, repo_id: int = 1) -> int:
        """Insert a new query with status='running'. Returns its id."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO queries (prompt, status, created_at, repo_id) VALUES (?, 'running', ?, ?)",
            (prompt, now, repo_id),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def complete_query(
        self, query_id: int, answer: str, evidence_json: str, duration_secs: float,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        estimated_cost: float | None = None,
    ) -> None:
        """Mark a query as completed with its answer, evidence, and optional usage."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE queries
               SET answer = ?, evidence = ?, status = 'completed',
                   completed_at = ?, duration_secs = ?,
                   prompt_tokens = ?, completion_tokens = ?, estimated_cost = ?
               WHERE id = ?""",
            (answer, evidence_json, now, duration_secs,
             prompt_tokens, completion_tokens, estimated_cost, query_id),
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

    def list_queries(self, limit: int = 50, repo_id: int = 1) -> list[dict]:
        """List recent queries (without answer/evidence for efficiency)."""
        cur = self._conn.execute(
            """SELECT id, prompt, status, created_at, duration_secs,
                      prompt_tokens, completion_tokens, estimated_cost
               FROM queries WHERE repo_id = ? ORDER BY created_at DESC LIMIT ?""",
            (repo_id, limit),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_completed_queries_since(
        self, after: str | None = None, repo_id: int = 1
    ) -> list[dict]:
        """Return completed queries, optionally only those completed after a timestamp."""
        if after:
            cur = self._conn.execute(
                """SELECT id, prompt, answer, evidence, duration_secs
                   FROM queries WHERE status = 'completed' AND completed_at > ?
                   AND repo_id = ?
                   ORDER BY completed_at DESC""",
                (after, repo_id),
            )
        else:
            cur = self._conn.execute(
                """SELECT id, prompt, answer, evidence, duration_secs
                   FROM queries WHERE status = 'completed' AND repo_id = ?
                   ORDER BY completed_at DESC""",
                (repo_id,),
            )
        return [dict(row) for row in cur.fetchall()]

    def insert_cached_query(
        self, prompt: str, answer: str, evidence_json: str,
        source_query_id: int, duration_secs: float, repo_id: int = 1,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        estimated_cost: float | None = None,
    ) -> int:
        """Insert a cached query entry pointing to its source."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            """INSERT INTO queries
               (prompt, answer, evidence, status, created_at, completed_at,
                duration_secs, source_query_id, repo_id,
                prompt_tokens, completion_tokens, estimated_cost)
               VALUES (?, ?, ?, 'cached', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (prompt, answer, evidence_json, now, now, duration_secs, source_query_id, repo_id,
             prompt_tokens, completion_tokens, estimated_cost),
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

    def count(self, table: str, repo_id: int = 1) -> int:
        # metadata and repos tables don't have repo_id
        if table in ("metadata", "repos"):
            cur = self._conn.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
        else:
            cur = self._conn.execute(
                f"SELECT count(*) FROM {table} WHERE repo_id = ?", (repo_id,)  # noqa: S608
            )
        return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()
