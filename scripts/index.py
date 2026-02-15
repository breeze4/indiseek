#!/usr/bin/env python3
"""CLI: Index a repository's TypeScript/TSX files into SQLite."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

# Ensure the package is importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from indiseek import config
from indiseek.indexer.parser import TypeScriptParser
from indiseek.storage.sqlite_store import SqliteStore


def get_tracked_files(repo_path: Path) -> list[Path]:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Index a TypeScript repository")
    parser.add_argument(
        "--scip-path",
        type=Path,
        default=None,
        help="Path to SCIP index file (default: {REPO_PATH}/index.scip)",
    )
    args = parser.parse_args()

    repo_path = config.REPO_PATH
    if not repo_path or not repo_path.is_dir():
        print(f"Error: REPO_PATH={repo_path} is not a valid directory.", file=sys.stderr)
        print("Set REPO_PATH in .env or environment.", file=sys.stderr)
        sys.exit(1)

    print(f"Indexing repository: {repo_path}")
    start = time.time()

    # Initialize storage
    store = SqliteStore(config.SQLITE_PATH)
    store.init_db()

    # Discover files
    ts_files = get_tracked_files(repo_path)
    print(f"Found {len(ts_files)} TypeScript/TSX files")

    # Parse with Tree-sitter
    ts_parser = TypeScriptParser()
    total_symbols = 0
    total_chunks = 0
    errors = 0

    for i, fpath in enumerate(ts_files, 1):
        relative = str(fpath.relative_to(repo_path))
        if i % 100 == 0 or i == len(ts_files):
            print(f"  Parsing {i}/{len(ts_files)}: {relative}")

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

    print(f"\nTree-sitter parsing complete:")
    print(f"  Files parsed: {len(ts_files)} ({errors} errors)")
    print(f"  Symbols extracted: {total_symbols}")
    print(f"  Chunks created: {total_chunks}")

    # Load SCIP cross-references if available
    scip_path = args.scip_path
    if scip_path is None:
        scip_path = repo_path / "index.scip"

    if scip_path.exists():
        print(f"\nLoading SCIP index: {scip_path}")
        from indiseek.indexer.scip import ScipLoader

        loader = ScipLoader(store)
        counts = loader.load(scip_path)
        print(f"  SCIP symbols loaded: {counts['symbols']}")
        print(f"  SCIP occurrences loaded: {counts['occurrences']}")
        print(f"  SCIP relationships loaded: {counts['relationships']}")
    else:
        print(f"\nNo SCIP index found at {scip_path}, skipping cross-references.")
        print("  Run: bash scripts/generate_scip.sh /path/to/repo")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Database: {config.SQLITE_PATH}")

    store.close()


if __name__ == "__main__":
    main()
