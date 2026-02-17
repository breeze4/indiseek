#!/usr/bin/env python3
"""CLI: Index a repository's TypeScript/TSX files into SQLite."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure the package is importable when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from indiseek import config
from indiseek.indexer.pipeline import get_tracked_ts_files, run_treesitter, run_scip, run_lexical, run_summarize_dirs
from indiseek.storage.sqlite_store import SqliteStore


def _resolve_repo(store: SqliteStore, repo_arg: str | None) -> tuple[int, Path]:
    """Resolve --repo argument to (repo_id, repo_path).

    If --repo is given, looks up by name or numeric ID.
    Otherwise defaults to repo_id=1 with REPO_PATH.
    """
    if repo_arg is None:
        # Legacy default
        repo_path = config.REPO_PATH
        if not repo_path or not repo_path.is_dir():
            print(f"Error: REPO_PATH={repo_path} is not a valid directory.", file=sys.stderr)
            print("Set REPO_PATH in .env or use --repo <name-or-id>.", file=sys.stderr)
            sys.exit(1)
        return 1, repo_path

    # Try numeric ID first
    try:
        repo_id = int(repo_arg)
        repo = store.get_repo(repo_id)
        if not repo:
            print(f"Error: No repo found with id={repo_id}.", file=sys.stderr)
            sys.exit(1)
        return repo_id, Path(repo["local_path"])
    except ValueError:
        pass

    # Try name lookup
    repo = store.get_repo_by_name(repo_arg)
    if not repo:
        print(f"Error: No repo found with name='{repo_arg}'.", file=sys.stderr)
        sys.exit(1)
    return repo["id"], Path(repo["local_path"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Index a TypeScript repository")
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Repository name or ID (default: repo_id=1 using REPO_PATH)",
    )
    parser.add_argument(
        "--scip-path",
        type=Path,
        default=None,
        help="Path to SCIP index file (default: {repo_path}/index.scip)",
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

    # Initialize storage
    store = SqliteStore(config.SQLITE_PATH)
    store.init_db()

    # Resolve repo
    repo_id, repo_path = _resolve_repo(store, args.repo)

    if not repo_path.is_dir():
        print(f"Error: repo path {repo_path} is not a valid directory.", file=sys.stderr)
        sys.exit(1)

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

    # Repo-specific storage paths
    tantivy_path = config.get_tantivy_path(repo_id)
    lancedb_table_name = config.get_lancedb_table_name(repo_id)

    # ── Dry run: report what would happen, then exit ──
    if args.dry_run:
        print(f"Repository: {repo_path} (repo_id={repo_id})")
        if args.filter:
            print(f"Filter: {args.filter}")
        print()

        print(f"[tree-sitter]  {len(ts_files)} files to parse (always re-parsed)")
        print(f"[scip]         {'yes' if scip_path.exists() else 'NO — not found at ' + str(scip_path)} (always re-loaded)")

        if args.embed:
            from indiseek.storage.vector_store import VectorStore
            vector_store = VectorStore(config.LANCEDB_PATH, dims=config.EMBEDDING_DIMS, table_name=lancedb_table_name)
            existing_embeds = len(vector_store.get_chunk_ids())
            current_chunks = store.count("chunks")
            new_to_embed = max(0, len(ts_files) - existing_embeds) if current_chunks == 0 else max(0, current_chunks - existing_embeds)
            print(f"[embed]        {existing_embeds} already embedded, ~{new_to_embed} new (Gemini API calls)")
        else:
            print("[embed]        skipped (no --embed)")

        if args.summarize:
            from indiseek.indexer.summarizer import Summarizer
            summarizer = Summarizer(store, repo_id=repo_id)
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
            print("[summarize]    skipped (no --summarize)")

        if args.lexical:
            current_chunks = store.count("chunks")
            print(f"[lexical]      will index all chunks in SQLite (currently {current_chunks}, rebuilt after parse)")
        else:
            print("[lexical]      skipped (no --lexical)")

        store.close()
        return

    # ── Real run ──
    print(f"Indexing repository: {repo_path} (repo_id={repo_id})")
    if args.filter:
        print(f"Filter: {args.filter}")
    print(f"Files: {len(ts_files)}")
    start = time.time()

    # Step 1: Tree-sitter parsing
    ts_result = run_treesitter(store, repo_path, path_filter=args.filter, repo_id=repo_id)
    print(f"\nTree-sitter parsing complete:")
    print(f"  Files parsed: {ts_result['files_parsed']} ({ts_result['errors']} errors)")
    print(f"  Symbols extracted: {ts_result['symbols']}")
    print(f"  Chunks created: {ts_result['chunks']}")
    print(f"  File contents stored: {ts_result['files_stored']}")

    # Step 2: SCIP cross-references
    if scip_path.exists():
        print(f"\nLoading SCIP index: {scip_path}")
        scip_result = run_scip(store, scip_path, repo_id=repo_id)
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

        vector_store = VectorStore(config.LANCEDB_PATH, dims=config.EMBEDDING_DIMS, table_name=lancedb_table_name)
        embedder = Embedder(store, vector_store)
        n_embedded = embedder.embed_all_chunks(path_filter=args.filter, repo_id=repo_id)
        print(f"  Chunks embedded: {n_embedded}")
        print(f"  LanceDB: {config.LANCEDB_PATH} (table: {lancedb_table_name})")

    # Step 4: Summarize files
    if args.summarize:
        if not config.GEMINI_API_KEY:
            print("\nError: --summarize requires GEMINI_API_KEY in .env", file=sys.stderr)
            sys.exit(1)

        print("\nSummarizing files...")
        from indiseek.indexer.summarizer import Summarizer

        summarizer = Summarizer(store, repo_id=repo_id)
        n_summarized = summarizer.summarize_repo(repo_path, path_filter=args.filter)
        print(f"  Files summarized: {n_summarized}")

        # Step 4b: Summarize directories (after file summaries exist)
        print("\nSummarizing directories...")
        dir_result = run_summarize_dirs(store, repo_id=repo_id)
        print(f"  Directories summarized: {dir_result['directories_summarized']}")

    # Step 5: Lexical index
    if args.lexical:
        print("\nBuilding lexical index...")
        lexical_result = run_lexical(store, tantivy_path, repo_id=repo_id)
        print(f"  Documents indexed in Tantivy: {lexical_result['documents_indexed']}")
        print(f"  Index path: {tantivy_path}")

    elapsed = time.time() - start

    # Update repo metadata on completion
    _update_repo_metadata(store, repo_id, repo_path)

    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Database: {config.SQLITE_PATH}")

    store.close()


def _update_repo_metadata(store: SqliteStore, repo_id: int, repo_path: Path) -> None:
    """Update repo's indexed_commit_sha and last_indexed_at after successful indexing."""
    now = datetime.now(timezone.utc).isoformat()

    # Try to get HEAD SHA
    head_sha = None
    try:
        from indiseek.git_utils import get_head_sha
        head_sha = get_head_sha(repo_path)
    except Exception:
        pass

    repo = store.get_repo(repo_id)
    if repo:
        updates: dict = {"last_indexed_at": now}
        if head_sha:
            updates["indexed_commit_sha"] = head_sha
            updates["current_commit_sha"] = head_sha
            updates["commits_behind"] = 0
        store.update_repo(repo_id, **updates)


if __name__ == "__main__":
    main()
