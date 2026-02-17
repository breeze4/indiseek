"""Pipeline step functions for indexing operations.

Each function wraps one step of the indexing pipeline, accepts an optional
on_progress callback, and returns a summary dict.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable

from indiseek.indexer.parser import TypeScriptParser
from indiseek.storage.sqlite_store import SqliteStore


def get_tracked_ts_files(repo_path: Path) -> list[Path]:
    """Get all .ts/.tsx files tracked by git, respecting .gitignore."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        cwd=repo_path,
        check=True,
    )
    files = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        p = repo_path / line
        if p.suffix in (".ts", ".tsx") and p.exists():
            files.append(p)
    return files


def run_treesitter(
    store: SqliteStore,
    repo_path: Path,
    path_filter: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Parse .ts/.tsx files with tree-sitter and store symbols/chunks.

    If path_filter is set, only clears and re-parses files under that prefix.
    Otherwise clears all index data.

    Returns {"files_parsed": N, "symbols": N, "chunks": N, "errors": N}.
    """
    ts_files = get_tracked_ts_files(repo_path)
    if path_filter:
        ts_files = [
            f for f in ts_files
            if str(f.relative_to(repo_path)).startswith(path_filter)
        ]
        store.clear_index_data_for_prefix(path_filter)
    else:
        store.clear_index_data()

    total = len(ts_files)
    ts_parser = TypeScriptParser()
    total_symbols = 0
    total_chunks = 0
    errors = 0

    for i, fpath in enumerate(ts_files, 1):
        relative = str(fpath.relative_to(repo_path))

        if on_progress:
            on_progress({
                "step": "treesitter", "current": i, "total": total,
                "file": relative,
            })
        elif i % 100 == 0 or i == total:
            print(f"  Parsing {i}/{total}: {relative}")

        try:
            symbols = ts_parser.parse_file(fpath, relative)
            chunks = ts_parser.chunk_file(fpath, relative)

            if symbols:
                store.insert_symbols(symbols)
            if chunks:
                store.insert_chunks(chunks)

            total_symbols += len(symbols)
            total_chunks += len(chunks)
        except Exception as e:
            errors += 1
            print(f"  Warning: Failed to parse {relative}: {e}", file=sys.stderr)

    return {
        "files_parsed": total,
        "symbols": total_symbols,
        "chunks": total_chunks,
        "errors": errors,
    }


def run_scip(
    store: SqliteStore,
    scip_path: Path,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Load a SCIP protobuf index into SQLite.

    Returns {"symbols": N, "occurrences": N, "relationships": N}.
    """
    from indiseek.indexer.scip import ScipLoader

    if on_progress:
        on_progress({"step": "scip", "status": "loading", "file": str(scip_path)})

    loader = ScipLoader(store)
    counts = loader.load(scip_path)

    if on_progress:
        on_progress({"step": "scip", "status": "done", **counts})

    return counts


def run_lexical(
    store: SqliteStore,
    tantivy_path: Path,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Build the Tantivy BM25 lexical index from SQLite chunks.

    Returns {"documents_indexed": N}.
    """
    from indiseek.indexer.lexical import LexicalIndexer

    if on_progress:
        on_progress({"step": "lexical", "status": "building"})

    lexical_indexer = LexicalIndexer(store, tantivy_path)
    n_indexed = lexical_indexer.build_index()

    if on_progress:
        on_progress({"step": "lexical", "status": "done", "documents_indexed": n_indexed})

    return {"documents_indexed": n_indexed}
