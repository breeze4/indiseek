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
from indiseek.tools.search_code import (
    CodeSearcher,
    HybridResult,
    QueryCache,
    compute_query_similarity,
    format_results,
    strip_file_paths,
)


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


# ── SqliteStore dashboard methods tests ──


class TestSqliteStoreDashboardMethods:
    def test_get_chunk_by_id(self, store):
        store.insert_chunks([
            Chunk(None, "src/main.ts", "hello", "function", 1, 10, "function hello() {}", 20),
        ])
        chunk = store.get_chunk_by_id(1)
        assert chunk is not None
        assert chunk["file_path"] == "src/main.ts"
        assert chunk["symbol_name"] == "hello"

    def test_get_chunk_by_id_not_found(self, store):
        assert store.get_chunk_by_id(999) is None

    def test_get_all_file_paths_from_chunks(self, store):
        store.insert_chunks([
            Chunk(None, "src/a.ts", "a", "function", 1, 5, "fn a", 4),
            Chunk(None, "src/a.ts", "b", "function", 6, 10, "fn b", 4),
            Chunk(None, "src/b.ts", "c", "function", 1, 5, "fn c", 4),
        ])
        paths = store.get_all_file_paths_from_chunks()
        assert paths == {"src/a.ts", "src/b.ts"}

    def test_get_all_file_paths_from_summaries(self, store):
        store.insert_file_summaries([
            ("src/a.ts", "File A", "ts", 10),
            ("src/b.ts", "File B", "ts", 5),
        ])
        paths = store.get_all_file_paths_from_summaries()
        assert paths == {"src/a.ts", "src/b.ts"}

    def test_get_file_summary(self, store):
        store.insert_file_summaries([
            ("src/main.ts", "Main entry point", "ts", 42),
        ])
        summary = store.get_file_summary("src/main.ts")
        assert summary is not None
        assert summary["summary"] == "Main entry point"
        assert summary["line_count"] == 42

    def test_get_file_summary_not_found(self, store):
        assert store.get_file_summary("nonexistent.ts") is None

    def test_clear_index_data_for_prefix(self, store):
        store.insert_chunks([
            Chunk(None, "src/a.ts", "a", "function", 1, 5, "fn a", 4),
            Chunk(None, "src/b.ts", "b", "function", 1, 5, "fn b", 4),
            Chunk(None, "lib/c.ts", "c", "function", 1, 5, "fn c", 4),
        ])
        store.insert_symbols([
            Symbol(None, "src/a.ts", "a", "function", 1, 0, 5, 1, None, None),
            Symbol(None, "lib/c.ts", "c", "function", 1, 0, 5, 1, None, None),
        ])
        counts = store.clear_index_data_for_prefix("src/")
        assert counts["chunks_deleted"] == 2
        assert counts["symbols_deleted"] == 1
        # lib/c.ts should still exist
        assert store.count("chunks") == 1
        assert store.count("symbols") == 1


# ── File contents storage tests ──


class TestFileContents:
    def test_insert_and_retrieve(self, store):
        content = "function hello() {\n  console.log('hi');\n}\n"
        store.insert_file_content("src/main.ts", content)
        retrieved = store.get_file_content("src/main.ts")
        assert retrieved == content

    def test_missing_path_returns_none(self, store):
        assert store.get_file_content("nonexistent.ts") is None

    def test_line_count_computed(self, store):
        content = "line1\nline2\nline3\n"
        store.insert_file_content("src/a.ts", content)
        cur = store._conn.execute(
            "SELECT line_count FROM file_contents WHERE file_path = ?", ("src/a.ts",)
        )
        assert cur.fetchone()[0] == 3

    def test_upsert_replaces(self, store):
        store.insert_file_content("src/a.ts", "old content")
        store.insert_file_content("src/a.ts", "new content")
        assert store.get_file_content("src/a.ts") == "new content"

    def test_clear_index_data_deletes_file_contents(self, store):
        store.insert_file_content("src/a.ts", "content")
        store.clear_index_data()
        assert store.get_file_content("src/a.ts") is None


# ── strip_file_paths tests ──


class TestStripFilePaths:
    def test_strips_trailing_file_path(self):
        assert strip_file_paths("handleHotUpdate packages/vite/src/node/css.ts") == "handleHotUpdate"

    def test_strips_path_prefix(self):
        assert strip_file_paths("path:src/server.ts createServer") == "createServer"

    def test_strips_file_prefix(self):
        assert strip_file_paths("file:index.ts exportDefault") == "exportDefault"

    def test_leaves_natural_language_unchanged(self):
        assert strip_file_paths("HMR CSS propagation") == "HMR CSS propagation"

    def test_leaves_single_word_unchanged(self):
        assert strip_file_paths("createServer") == "createServer"

    def test_strips_trailing_path_keeps_words(self):
        assert strip_file_paths("module graph invalidation src/node/moduleGraph.ts") == "module graph invalidation"


# ── query similarity / cache tests ──


class TestQuerySimilarity:
    def test_identical(self):
        assert compute_query_similarity("hello world", "hello world") == 1.0

    def test_same_tokens_different_order(self):
        assert compute_query_similarity("hello world foo", "foo world hello") > 0.8

    def test_subset(self):
        assert compute_query_similarity("hello world", "hello world foo bar") > 0.3

    def test_completely_different(self):
        assert compute_query_similarity("alpha beta", "gamma delta") < 0.3

    def test_case_insensitive(self):
        assert compute_query_similarity("Hello World", "hello world") == 1.0


class TestQueryCache:
    def test_get_empty_cache(self):
        cache = QueryCache()
        assert cache.get("anything") is None

    def test_put_then_get_exact(self):
        cache = QueryCache()
        cache.put("hello world", "result-1")
        assert cache.get("hello world") == "result-1"

    def test_put_then_get_similar(self):
        cache = QueryCache()
        cache.put("HMR CSS hot update", "result-2")
        # Same tokens, different order — similarity > 0.8
        assert cache.get("CSS HMR hot update") == "result-2"

    def test_put_then_get_dissimilar(self):
        cache = QueryCache()
        cache.put("HMR CSS propagation", "result-3")
        assert cache.get("createServer module graph") is None


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

    def test_default_line_cap(self, tmp_path):
        """Files over 500 lines are truncated with a notice."""
        repo = tmp_path / "cap_repo"
        repo.mkdir()
        long_file = repo / "long.ts"
        long_file.write_text("\n".join(f"line {i}" for i in range(1, 601)))
        result = read_file(repo, "long.ts")
        assert "showing first 500 of 600 lines" in result
        assert "start_line/end_line" in result
        # Line 500 should be present, line 501 should not
        assert "line 500" in result
        assert "line 501" not in result

    def test_no_cap_with_explicit_range(self, tmp_path):
        """Explicit start_line/end_line bypasses the default cap."""
        repo = tmp_path / "cap_repo2"
        repo.mkdir()
        long_file = repo / "long.ts"
        long_file.write_text("\n".join(f"line {i}" for i in range(1, 301)))
        result = read_file(repo, "long.ts", start_line=1, end_line=300)
        assert "showing first 200" not in result
        assert "line 300" in result

    def test_short_file_no_truncation(self, repo_dir):
        """Files under 200 lines are not truncated."""
        result = read_file(repo_dir, "src/main.ts")
        assert "showing first" not in result
