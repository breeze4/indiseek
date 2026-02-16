"""Tests for agent tools: read_map, search_code, resolve_symbol, read_file."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from indiseek.storage.sqlite_store import Chunk, SqliteStore, Symbol
from indiseek.tools.read_file import read_file
from indiseek.tools.read_map import read_map
from indiseek.tools.resolve_symbol import (
    _extract_name_from_scip_symbol,
    resolve_symbol,
)
from indiseek.tools.search_code import CodeSearcher, HybridResult, format_results


# ── Fixtures ──


@pytest.fixture
def store(tmp_path):
    """Create a fresh SqliteStore with schema initialized."""
    db = SqliteStore(tmp_path / "test.db")
    db.init_db()
    return db


@pytest.fixture
def repo_dir(tmp_path):
    """Create a temporary repo directory with sample files."""
    repo = tmp_path / "repo"
    repo.mkdir()

    (repo / "src").mkdir()
    (repo / "src" / "main.ts").write_text(
        "function hello() {\n  console.log('hello');\n}\n\nexport { hello };\n"
    )
    (repo / "src" / "utils.ts").write_text(
        "export function add(a: number, b: number): number {\n  return a + b;\n}\n"
    )
    (repo / "README.md").write_text("# Test project\n")

    return repo


# ── read_map tests ──


class TestReadMap:
    def test_empty_store(self, store):
        result = read_map(store)
        assert "No file summaries" in result

    def test_full_tree(self, store):
        store.insert_file_summaries([
            ("src/main.ts", "Main entry point", "ts", 10),
            ("src/utils.ts", "Utility functions", "ts", 5),
            ("README.md", "Project readme", "md", 3),
        ])
        result = read_map(store)
        assert "Repository map:" in result
        assert "src/" in result
        assert "main.ts" in result
        assert "Main entry point" in result
        assert "README.md" in result

    def test_scoped_to_directory(self, store):
        store.insert_file_summaries([
            ("src/main.ts", "Main entry point", "ts", 10),
            ("src/utils.ts", "Utility functions", "ts", 5),
            ("lib/helper.ts", "Helper module", "ts", 3),
        ])
        result = read_map(store, path="src")
        assert "Directory: src" in result
        assert "main.ts" in result
        assert "helper.ts" not in result

    def test_scoped_nonexistent_directory(self, store):
        store.insert_file_summaries([
            ("src/main.ts", "Main entry point", "ts", 10),
        ])
        result = read_map(store, path="nonexistent")
        assert "No files found" in result

    def test_nested_directories(self, store):
        store.insert_file_summaries([
            ("a/b/c.ts", "Deep file", "ts", 1),
            ("a/b/d.ts", "Another deep file", "ts", 1),
            ("a/e.ts", "Shallow file", "ts", 1),
        ])
        result = read_map(store)
        assert "a/" in result
        assert "b/" in result
        assert "Deep file" in result


# ── search_code format_results tests ──


class TestSearchCodeFormatting:
    def test_empty_results(self):
        result = format_results([], "test query")
        assert "No results found" in result
        assert "test query" in result

    def test_formats_results(self):
        results = [
            HybridResult(
                chunk_id=1,
                file_path="src/main.ts",
                symbol_name="hello",
                chunk_type="function",
                content="function hello() { console.log('hello'); }",
                score=0.95,
                match_type="hybrid",
            ),
        ]
        formatted = format_results(results, "hello function")
        assert "src/main.ts" in formatted
        assert "hello" in formatted
        assert "function" in formatted
        assert "hybrid" in formatted
        assert "0.9500" in formatted

    def test_truncates_long_content(self):
        results = [
            HybridResult(
                chunk_id=1,
                file_path="src/big.ts",
                symbol_name=None,
                chunk_type="module",
                content="x" * 500,
                score=0.5,
                match_type="lexical",
            ),
        ]
        formatted = format_results(results, "query")
        assert "..." in formatted

    def test_no_symbol_name(self):
        results = [
            HybridResult(
                chunk_id=1,
                file_path="src/mod.ts",
                symbol_name=None,
                chunk_type="module",
                content="some code",
                score=0.7,
                match_type="semantic",
            ),
        ]
        formatted = format_results(results, "query")
        assert "src/mod.ts" in formatted
        # Should not contain empty brackets
        assert "[]" not in formatted

    def test_multiple_results(self):
        results = [
            HybridResult(chunk_id=1, file_path="a.ts", symbol_name="a", chunk_type="function",
                         content="fn a", score=0.9, match_type="semantic"),
            HybridResult(chunk_id=2, file_path="b.ts", symbol_name="b", chunk_type="function",
                         content="fn b", score=0.8, match_type="lexical"),
        ]
        formatted = format_results(results, "test")
        assert "2 result(s)" in formatted
        assert "1." in formatted
        assert "2." in formatted


# ── resolve_symbol tests ──


class TestResolveSymbol:
    def test_invalid_action(self, store):
        result = resolve_symbol(store, "foo", "invalid")
        assert "Invalid action" in result

    def test_definition_from_scip(self, store):
        sym_id = store.insert_scip_symbol("npm . pkg 1.0 src/`main`/`createServer`().")
        store.insert_scip_occurrences([
            (sym_id, "src/server.ts", 42, 0, 42, 12, "definition"),
        ])
        result = resolve_symbol(store, "createServer", "definition")
        assert "SCIP" in result
        assert "src/server.ts:42" in result

    def test_definition_fallback_to_treesitter(self, store):
        store.insert_symbols([
            Symbol(None, "src/main.ts", "hello", "function", 10, 0, 20, 1, "function hello()", None),
        ])
        result = resolve_symbol(store, "hello", "definition")
        assert "tree-sitter" in result
        assert "src/main.ts:10" in result
        assert "function" in result

    def test_definition_not_found(self, store):
        result = resolve_symbol(store, "nonexistent", "definition")
        assert "No definition found" in result

    def test_references_from_scip(self, store):
        sym_id = store.insert_scip_symbol("npm . pkg 1.0 src/`mod`/`hello`().")
        store.insert_scip_occurrences([
            (sym_id, "src/a.ts", 10, 0, 10, 5, "reference"),
            (sym_id, "src/b.ts", 20, 0, 20, 5, "reference"),
        ])
        result = resolve_symbol(store, "hello", "references")
        assert "SCIP" in result
        assert "2 result(s)" in result
        assert "src/a.ts:10" in result
        assert "src/b.ts:20" in result

    def test_references_fallback_to_treesitter(self, store):
        store.insert_symbols([
            Symbol(None, "src/main.ts", "hello", "function", 10, 0, 20, 1, None, None),
        ])
        result = resolve_symbol(store, "hello", "references")
        assert "tree-sitter" in result
        assert "no cross-ref data" in result

    def test_references_not_found(self, store):
        result = resolve_symbol(store, "nonexistent", "references")
        assert "No references found" in result

    def test_callers(self, store):
        # Target symbol
        sym_id = store.insert_scip_symbol("npm . pkg 1.0 src/`mod`/`targetFn`().")
        store.insert_scip_occurrences([
            (sym_id, "src/caller.ts", 15, 0, 15, 8, "reference"),
        ])
        # Enclosing tree-sitter symbol at that location
        store.insert_symbols([
            Symbol(None, "src/caller.ts", "callerFn", "function", 10, 0, 30, 1, None, None),
        ])
        result = resolve_symbol(store, "targetFn", "callers")
        assert "callerFn" in result
        assert "src/caller.ts:10" in result

    def test_callers_no_refs(self, store):
        result = resolve_symbol(store, "noRefs", "callers")
        assert "No callers found" in result

    def test_callees(self, store):
        # The target function defined in tree-sitter
        store.insert_symbols([
            Symbol(None, "src/main.ts", "myFunc", "function", 10, 0, 30, 1, None, None),
        ])
        # A symbol called within myFunc's range
        callee_id = store.insert_scip_symbol("npm . pkg 1.0 src/`utils`/`helperFn`().")
        store.insert_scip_occurrences([
            (callee_id, "src/main.ts", 15, 0, 15, 8, "reference"),
        ])
        result = resolve_symbol(store, "myFunc", "callees")
        assert "helperFn" in result
        assert "src/main.ts:15" in result

    def test_callees_no_definition(self, store):
        result = resolve_symbol(store, "undefinedFunc", "callees")
        assert "No definition found" in result


class TestExtractNameFromScipSymbol:
    def test_backtick_name(self):
        assert _extract_name_from_scip_symbol(
            "npm . vite 5.0.0 src/`module`/`createServer`()."
        ) == "createServer"

    def test_single_backtick(self):
        assert _extract_name_from_scip_symbol(
            "npm . pkg 1.0 `simpleName`."
        ) == "simpleName"

    def test_no_backticks(self):
        result = _extract_name_from_scip_symbol("something plain")
        assert result == "plain"

    def test_empty_string(self):
        result = _extract_name_from_scip_symbol("")
        assert result == ""


# ── read_file tests ──


class TestReadFile:
    def test_read_entire_file(self, repo_dir):
        result = read_file(repo_dir, "src/main.ts")
        assert "File: src/main.ts" in result
        assert "function hello()" in result
        assert "console.log" in result

    def test_read_with_line_range(self, repo_dir):
        result = read_file(repo_dir, "src/main.ts", start_line=1, end_line=2)
        assert "lines 1-2" in result
        assert "function hello()" in result
        assert "console.log" in result
        # Line 3 should not be present
        assert "export" not in result

    def test_read_with_start_only(self, repo_dir):
        result = read_file(repo_dir, "src/main.ts", start_line=3)
        assert "}" in result

    def test_read_with_end_only(self, repo_dir):
        result = read_file(repo_dir, "src/main.ts", end_line=2)
        assert "function hello()" in result

    def test_line_numbers_present(self, repo_dir):
        result = read_file(repo_dir, "src/main.ts")
        assert "     1 |" in result

    def test_file_not_found(self, repo_dir):
        result = read_file(repo_dir, "nonexistent.ts")
        assert "Error" in result
        assert "not found" in result

    def test_directory_not_file(self, repo_dir):
        result = read_file(repo_dir, "src")
        assert "Error" in result
        assert "not a file" in result

    def test_path_outside_repo(self, repo_dir):
        result = read_file(repo_dir, "../../etc/passwd")
        assert "Error" in result
        assert "outside" in result

    def test_path_traversal_blocked(self, repo_dir):
        result = read_file(repo_dir, "../../../etc/passwd")
        assert "Error" in result
        assert "outside" in result
