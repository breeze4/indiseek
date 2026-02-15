"""Tests for Tree-sitter parser and SQLite storage."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from indiseek.indexer.parser import TypeScriptParser
from indiseek.storage.sqlite_store import SqliteStore


@pytest.fixture
def store(tmp_path: Path) -> SqliteStore:
    s = SqliteStore(tmp_path / "test.db")
    s.init_db()
    return s


@pytest.fixture
def parser() -> TypeScriptParser:
    return TypeScriptParser()


@pytest.fixture
def sample_ts_file(tmp_path: Path) -> Path:
    code = """\
import { createServer } from 'vite'

interface Config {
  port: number;
  host: string;
}

export function createApp(config: Config): void {
  const server = createServer(config);
  server.listen();
}

class HMREngine {
  private watchers: Map<string, Function> = new Map();

  handleUpdate(file: string): void {
    const watcher = this.watchers.get(file);
    if (watcher) watcher();
  }

  register(file: string, cb: Function): void {
    this.watchers.set(file, cb);
  }
}

type ModuleId = string;

enum LogLevel {
  Debug,
  Info,
  Warn,
  Error,
}

export const DEFAULT_PORT = 3000;
"""
    p = tmp_path / "sample.ts"
    p.write_text(code)
    return p


@pytest.fixture
def sample_tsx_file(tmp_path: Path) -> Path:
    code = """\
interface Props {
  name: string;
}

export function Greeting({ name }: Props) {
  return <div>Hello {name}</div>;
}
"""
    p = tmp_path / "component.tsx"
    p.write_text(code)
    return p


class TestSqliteStore:
    def test_init_creates_tables(self, store: SqliteStore) -> None:
        # All tables should exist
        for table in ["symbols", "chunks", "scip_symbols", "scip_occurrences",
                      "scip_relationships", "file_summaries"]:
            assert store.count(table) == 0

    def test_insert_and_query_symbols(self, store: SqliteStore) -> None:
        from indiseek.storage.sqlite_store import Symbol

        sym = Symbol(
            id=None,
            file_path="src/index.ts",
            name="createServer",
            kind="function",
            start_line=10,
            start_col=0,
            end_line=25,
            end_col=1,
            signature="export function createServer(config: Config): Server",
        )
        store.insert_symbols([sym])

        assert store.count("symbols") == 1
        results = store.get_symbols_by_name("createServer")
        assert len(results) == 1
        assert results[0]["kind"] == "function"
        assert results[0]["file_path"] == "src/index.ts"

    def test_insert_and_query_chunks(self, store: SqliteStore) -> None:
        from indiseek.storage.sqlite_store import Chunk

        chunk = Chunk(
            id=None,
            file_path="src/index.ts",
            symbol_name="createServer",
            chunk_type="function",
            start_line=10,
            end_line=25,
            content="function createServer() { ... }",
            token_estimate=8,
        )
        store.insert_chunks([chunk])

        assert store.count("chunks") == 1
        results = store.get_chunks_by_file("src/index.ts")
        assert len(results) == 1
        assert results[0]["symbol_name"] == "createServer"

    def test_get_symbols_by_file(self, store: SqliteStore) -> None:
        from indiseek.storage.sqlite_store import Symbol

        syms = [
            Symbol(None, "a.ts", "foo", "function", 1, 0, 5, 1, None),
            Symbol(None, "a.ts", "bar", "function", 10, 0, 15, 1, None),
            Symbol(None, "b.ts", "baz", "function", 1, 0, 5, 1, None),
        ]
        store.insert_symbols(syms)

        results = store.get_symbols_by_file("a.ts")
        assert len(results) == 2
        names = {r["name"] for r in results}
        assert names == {"foo", "bar"}


class TestTypeScriptParser:
    def test_parse_file_extracts_symbols(
        self, parser: TypeScriptParser, sample_ts_file: Path
    ) -> None:
        symbols = parser.parse_file(sample_ts_file, "sample.ts")
        names = {s.name for s in symbols}

        # Should find: Config (interface), createApp (function),
        # HMREngine (class), handleUpdate (method), register (method),
        # ModuleId (type), LogLevel (enum), DEFAULT_PORT (exported var)
        assert "Config" in names
        assert "createApp" in names
        assert "HMREngine" in names
        assert "handleUpdate" in names
        assert "register" in names
        assert "ModuleId" in names
        assert "LogLevel" in names
        assert "DEFAULT_PORT" in names

    def test_parse_file_symbol_kinds(
        self, parser: TypeScriptParser, sample_ts_file: Path
    ) -> None:
        symbols = parser.parse_file(sample_ts_file, "sample.ts")
        by_name = {s.name: s for s in symbols}

        assert by_name["Config"].kind == "interface"
        assert by_name["createApp"].kind == "function"
        assert by_name["HMREngine"].kind == "class"
        assert by_name["handleUpdate"].kind == "method"
        assert by_name["ModuleId"].kind == "type"
        assert by_name["LogLevel"].kind == "enum"
        assert by_name["DEFAULT_PORT"].kind == "variable"

    def test_parse_file_line_numbers(
        self, parser: TypeScriptParser, sample_ts_file: Path
    ) -> None:
        symbols = parser.parse_file(sample_ts_file, "sample.ts")
        by_name = {s.name: s for s in symbols}

        # createApp (via export statement) starts at line 8 (1-indexed)
        assert by_name["createApp"].start_line == 8

    def test_chunk_file_produces_chunks(
        self, parser: TypeScriptParser, sample_ts_file: Path
    ) -> None:
        chunks = parser.chunk_file(sample_ts_file, "sample.ts")

        # Should get chunks for each symbol
        assert len(chunks) > 0
        chunk_names = {c.symbol_name for c in chunks if c.symbol_name}
        assert "createApp" in chunk_names
        assert "HMREngine" in chunk_names

    def test_chunk_file_content_not_empty(
        self, parser: TypeScriptParser, sample_ts_file: Path
    ) -> None:
        chunks = parser.chunk_file(sample_ts_file, "sample.ts")
        for chunk in chunks:
            assert chunk.content.strip(), f"Empty chunk: {chunk}"
            assert chunk.token_estimate > 0

    def test_parse_tsx(
        self, parser: TypeScriptParser, sample_tsx_file: Path
    ) -> None:
        symbols = parser.parse_file(sample_tsx_file, "component.tsx")
        names = {s.name for s in symbols}
        assert "Props" in names
        assert "Greeting" in names

    def test_chunk_empty_file_returns_module_chunk(
        self, parser: TypeScriptParser, tmp_path: Path
    ) -> None:
        """A file with no recognized symbols should still produce a module-level chunk."""
        p = tmp_path / "empty.ts"
        p.write_text("// just a comment\n")
        chunks = parser.chunk_file(p, "empty.ts")
        assert len(chunks) == 1
        assert chunks[0].chunk_type == "module"


class TestIntegration:
    def test_parse_and_store(
        self, parser: TypeScriptParser, store: SqliteStore, sample_ts_file: Path
    ) -> None:
        """End-to-end: parse a file and store results in SQLite."""
        symbols = parser.parse_file(sample_ts_file, "sample.ts")
        chunks = parser.chunk_file(sample_ts_file, "sample.ts")

        store.insert_symbols(symbols)
        store.insert_chunks(chunks)

        assert store.count("symbols") > 0
        assert store.count("chunks") > 0

        results = store.get_symbols_by_name("createApp")
        assert len(results) == 1
        assert results[0]["kind"] == "function"
