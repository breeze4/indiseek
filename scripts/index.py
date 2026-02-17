#!/usr/bin/env python3
"""CLI: Index a repository's TypeScript/TSX files into SQLite."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure the package is importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from indiseek import config
from indiseek.indexer.pipeline import get_tracked_ts_files, run_treesitter, run_scip, run_lexical, run_summarize_dirs
from indiseek.storage.sqlite_store import SqliteStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Index a TypeScript repository")
    parser.add_argument(
        "--scip-path",
        type=Path,
        default=None,
        help="Path to SCIP index file (default: {REPO_PATH}/index.scip)",
    )
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Embed chunks using Gemini and store in LanceDB (requires GEMINI_API_KEY)",
    )
    parser.add_argument(
        "--summarize",
        action="store_true",
        help="LLM-summarize each source file to build a map (requires GEMINI_API_KEY)",
    )
    parser.add_argument(
        "--lexical",
        action="store_true",
        help="Build a Tantivy BM25 lexical index over code chunks",
    )
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        help="Path prefix to restrict indexing (e.g. packages/vite/src)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what each stage would do without executing anything",
    )
    args = parser.parse_args()

    repo_path = config.REPO_PATH
    if not repo_path or not repo_path.is_dir():
        print(f"Error: REPO_PATH={repo_path} is not a valid directory.", file=sys.stderr)
        print("Set REPO_PATH in .env or environment.", file=sys.stderr)
        sys.exit(1)

    # Initialize storage
    store = SqliteStore(config.SQLITE_PATH)
    store.init_db()

    # Discover files
    ts_files = get_tracked_ts_files(repo_path)
    if args.filter:
        ts_files = [
            f for f in ts_files
            if str(f.relative_to(repo_path)).startswith(args.filter)
        ]

    # Resolve SCIP path
    scip_path = args.scip_path
    if scip_path is None:
        scip_path = repo_path / "index.scip"

    # ── Dry run: report what would happen, then exit ──
    if args.dry_run:
        print(f"Repository: {repo_path}")
        if args.filter:
            print(f"Filter: {args.filter}")
        print()

        print(f"[tree-sitter]  {len(ts_files)} files to parse (always re-parsed)")
        print(f"[scip]         {'yes' if scip_path.exists() else 'NO — not found at ' + str(scip_path)} (always re-loaded)")

        if args.embed:
            from indiseek.storage.vector_store import VectorStore
            vector_store = VectorStore(config.LANCEDB_PATH, dims=config.EMBEDDING_DIMS)
            existing_embeds = len(vector_store.get_chunk_ids())
            # After re-parse, chunk count will match ts_files; estimate from current DB
            current_chunks = store.count("chunks")
            new_to_embed = max(0, len(ts_files) - existing_embeds) if current_chunks == 0 else max(0, current_chunks - existing_embeds)
            print(f"[embed]        {existing_embeds} already embedded, ~{new_to_embed} new (Gemini API calls)")
        else:
            print(f"[embed]        skipped (no --embed)")

        if args.summarize:
            from indiseek.indexer.summarizer import Summarizer
            summarizer = Summarizer(store)
            source_files = summarizer._get_source_files(repo_path)
            if args.filter:
                source_files = [
                    f for f in source_files
                    if str(f.relative_to(repo_path)).startswith(args.filter)
                ]
            existing_summaries = summarizer._get_summarized_paths()
            already = sum(1 for f in source_files if str(f.relative_to(repo_path)) in existing_summaries)
            new_to_summarize = len(source_files) - already
            print(f"[summarize]    {already} already summarized, {new_to_summarize} new (Gemini API calls)")
        else:
            print(f"[summarize]    skipped (no --summarize)")

        if args.lexical:
            current_chunks = store.count("chunks")
            print(f"[lexical]      will index all chunks in SQLite (currently {current_chunks}, rebuilt after parse)")
        else:
            print(f"[lexical]      skipped (no --lexical)")

        store.close()
        return

    # ── Real run ──
    print(f"Indexing repository: {repo_path}")
    if args.filter:
        print(f"Filter: {args.filter}")
    print(f"Files: {len(ts_files)}")
    start = time.time()

    # Step 1: Tree-sitter parsing
    ts_result = run_treesitter(store, repo_path, path_filter=args.filter)
    print(f"\nTree-sitter parsing complete:")
    print(f"  Files parsed: {ts_result['files_parsed']} ({ts_result['errors']} errors)")
    print(f"  Symbols extracted: {ts_result['symbols']}")
    print(f"  Chunks created: {ts_result['chunks']}")
    print(f"  File contents stored: {ts_result['files_stored']}")

    # Step 2: SCIP cross-references
    if scip_path.exists():
        print(f"\nLoading SCIP index: {scip_path}")
        scip_result = run_scip(store, scip_path)
        print(f"  SCIP symbols loaded: {scip_result['symbols']}")
        print(f"  SCIP occurrences loaded: {scip_result['occurrences']}")
        print(f"  SCIP relationships loaded: {scip_result['relationships']}")
    else:
        print(f"\nNo SCIP index found at {scip_path}, skipping cross-references.")
        print("  Run: bash scripts/generate_scip.sh /path/to/repo")

    # Step 3: Embed chunks
    if args.embed:
        if not config.GEMINI_API_KEY:
            print("\nError: --embed requires GEMINI_API_KEY in .env", file=sys.stderr)
            sys.exit(1)

        print("\nEmbedding chunks...")
        from indiseek.indexer.embedder import Embedder
        from indiseek.storage.vector_store import VectorStore

        vector_store = VectorStore(config.LANCEDB_PATH, dims=config.EMBEDDING_DIMS)
        embedder = Embedder(store, vector_store)
        n_embedded = embedder.embed_all_chunks(path_filter=args.filter)
        print(f"  Chunks embedded: {n_embedded}")
        print(f"  LanceDB: {config.LANCEDB_PATH}")

    # Step 4: Summarize files
    if args.summarize:
        if not config.GEMINI_API_KEY:
            print("\nError: --summarize requires GEMINI_API_KEY in .env", file=sys.stderr)
            sys.exit(1)

        print("\nSummarizing files...")
        from indiseek.indexer.summarizer import Summarizer

        summarizer = Summarizer(store)
        n_summarized = summarizer.summarize_repo(repo_path, path_filter=args.filter)
        print(f"  Files summarized: {n_summarized}")

        # Step 4b: Summarize directories (after file summaries exist)
        print("\nSummarizing directories...")
        dir_result = run_summarize_dirs(store)
        print(f"  Directories summarized: {dir_result['directories_summarized']}")

    # Step 5: Lexical index
    if args.lexical:
        print("\nBuilding lexical index...")
        lexical_result = run_lexical(store, config.TANTIVY_PATH)
        print(f"  Documents indexed in Tantivy: {lexical_result['documents_indexed']}")
        print(f"  Index path: {config.TANTIVY_PATH}")

    elapsed = time.time() - start
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Database: {config.SQLITE_PATH}")

    store.close()


if __name__ == "__main__":
    main()
