"""API router — health, query, stats, tree, files, chunks, search, operations, SSE."""

from __future__ import annotations

import json
import logging
import queue
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from indiseek import config
from indiseek.agent.strategy import strategy_registry
from indiseek.api.task_manager import TaskManager
from indiseek.git_utils import GitError

# Trigger strategy registration on import
import indiseek.agent.loop  # noqa: F401, E402
import indiseek.agent.multi  # noqa: F401, E402

logger = logging.getLogger(__name__)

router = APIRouter()
_task_manager = TaskManager()

# Lazy-initialized strategies, keyed by (strategy_name, repo_id)
_strategy_cache: dict[tuple[str, int], object] = {}


def _get_strategy(name: str, repo_id: int = 1):
    """Get or create a cached strategy instance."""
    key = (name, repo_id)
    if key not in _strategy_cache:
        logger.info("Initializing strategy %r for repo_id=%d...", name, repo_id)
        t0 = time.perf_counter()
        _strategy_cache[key] = strategy_registry.create(name, repo_id=repo_id)
        logger.info("Strategy %r ready (%.2fs)", name, time.perf_counter() - t0)
    return _strategy_cache[key]


def _resolve_strategy_name(prompt: str, mode: str) -> str:
    """Resolve 'auto' mode to a concrete strategy name."""
    if mode == "auto":
        return strategy_registry.auto_select(prompt)
    return mode


# ── Health ──


@router.get("/health")
def health():
    return {"status": "ok"}


# ── Strategies ──


@router.get("/strategies")
def list_strategies():
    """List available query strategies."""
    # Ensure strategies are registered by importing the modules
    import indiseek.agent.loop  # noqa: F401
    import indiseek.agent.multi  # noqa: F401
    return {"strategies": strategy_registry.list_strategies()}


# ── Synchronous query (for curl / direct API usage) ──


class SyncQueryRequest(BaseModel):
    prompt: str
    repo_id: int = 1
    mode: str = "auto"  # "auto", "single", "multi"


class EvidenceStepResponse(BaseModel):
    step: str
    detail: str


class UsageResponse(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    requests: int = 0
    estimated_cost: float = 0.0
    model: str = ""


class SyncQueryResponse(BaseModel):
    answer: str
    evidence: list[EvidenceStepResponse]
    usage: UsageResponse | None = None


@router.post("/query", response_model=SyncQueryResponse)
def sync_query(req: SyncQueryRequest):
    """Synchronous query — blocks until the agent finishes. Saves to query history."""
    logger.info("POST /query prompt=%r", req.prompt[:120])

    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")

    # Validate strategy name
    strategy_name = _resolve_strategy_name(req.prompt, req.mode)
    available = strategy_registry.list_strategies()
    if strategy_name not in available:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy {strategy_name!r}. Available: {', '.join(available)}",
        )

    query_id = store.insert_query(req.prompt, repo_id=req.repo_id)
    t0 = time.perf_counter()
    try:
        logger.info("Using strategy %r", strategy_name)
        strategy = _get_strategy(strategy_name, repo_id=req.repo_id)
        result = strategy.run(req.prompt)
        elapsed = time.perf_counter() - t0
        logger.info(
            "Query complete: %d evidence steps, %d char answer, %.2fs total",
            len(result.evidence), len(result.answer), elapsed,
        )
        evidence = [
            {"tool": e.tool, "args": e.args, "summary": e.summary}
            for e in result.evidence
        ]
        usage_dict = result.metadata.get("usage", {})
        store.complete_query(
            query_id, result.answer, json.dumps(evidence), elapsed,
            prompt_tokens=usage_dict.get("prompt_tokens"),
            completion_tokens=usage_dict.get("completion_tokens"),
            estimated_cost=usage_dict.get("estimated_cost"),
        )
        return SyncQueryResponse(
            answer=result.answer,
            evidence=[
                EvidenceStepResponse(
                    step=f"{e.tool}({', '.join(f'{k}={v!r}' for k, v in e.args.items())})",
                    detail=e.summary,
                )
                for e in result.evidence
            ],
            usage=UsageResponse(**usage_dict) if usage_dict else None,
        )
    except Exception as e:
        elapsed = time.perf_counter() - t0
        store.fail_query(query_id, str(e))
        logger.exception("Agent error after %.2fs", elapsed)
        raise HTTPException(status_code=500, detail=str(e))


# ── Lazy-initialized stores ──


def _get_sqlite_store():
    from indiseek.storage.sqlite_store import SqliteStore
    if not config.SQLITE_PATH.exists():
        return None
    store = SqliteStore(config.SQLITE_PATH)
    store.init_db()
    return store


def _get_vector_store(repo_id: int = 1):
    from indiseek.storage.vector_store import VectorStore
    try:
        table_name = config.get_lancedb_table_name(repo_id)
        return VectorStore(config.LANCEDB_PATH, dims=config.EMBEDDING_DIMS, table_name=table_name)
    except Exception:
        return None


def _get_lexical_indexer(store, repo_id: int = 1):
    from indiseek.indexer.lexical import LexicalIndexer
    try:
        tantivy_path = config.get_tantivy_path(repo_id)
        indexer = LexicalIndexer(store, tantivy_path)
        indexer.open_index()
        return indexer
    except Exception:
        return None


# ── Repos ──


class CreateRepoRequest(BaseModel):
    name: str
    url: str
    shallow: bool = True


@router.get("/repos")
def list_repos():
    """List all repositories."""
    store = _get_sqlite_store()
    if not store:
        return []
    return store.list_repos()


@router.post("/repos")
def create_repo(req: CreateRepoRequest):
    """Add a new repository by cloning from URL."""
    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")

    # Check for duplicate name
    if store.get_repo_by_name(req.name):
        raise HTTPException(status_code=409, detail=f"Repo '{req.name}' already exists")

    if _task_manager.has_running_task():
        raise HTTPException(status_code=409, detail="A task is already running")

    from indiseek.git_utils import clone_repo
    from indiseek.storage.sqlite_store import SqliteStore

    def _run():
        s = SqliteStore(config.SQLITE_PATH)
        s.init_db()
        repo_id = s.insert_repo(req.name, str(dest), url=req.url)
        try:
            clone_repo(req.url, dest, shallow=req.shallow)
        except GitError as e:
            s.delete_repo(repo_id)
            raise RuntimeError(str(e)) from e
        return {"repo_id": repo_id, "name": req.name, "local_path": str(dest)}

    dest = config.REPOS_DIR / req.name
    task_id = _task_manager.submit("clone", _run)
    return {"task_id": task_id, "name": "clone", "status": "running"}


@router.get("/repos/{repo_id}")
def get_repo(repo_id: int):
    """Get a single repository by ID."""
    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")
    repo = store.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    return repo


@router.delete("/repos/{repo_id}")
def delete_repo(repo_id: int):
    """Delete a repository record."""
    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")
    repo = store.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")
    store.delete_repo(repo_id)
    return {"deleted": True, "repo_id": repo_id}


@router.post("/repos/{repo_id}/check")
def check_repo_freshness(repo_id: int):
    """Check how far behind the remote a repo is."""
    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")
    repo = store.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    from indiseek.git_utils import (
        count_commits_between,
        fetch_remote,
        get_changed_files,
        get_head_sha,
    )

    repo_path = Path(repo["local_path"])
    if not repo_path.is_dir():
        raise HTTPException(status_code=400, detail="Repo local path not found on disk")

    try:
        fetch_remote(repo_path)
    except GitError as e:
        raise HTTPException(status_code=500, detail=f"git fetch failed: {e}")

    # Detect default remote branch (origin/main or origin/master)
    try:
        remote_sha = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            capture_output=True, text=True, cwd=repo_path,
        )
        if remote_sha.returncode != 0:
            remote_sha = subprocess.run(
                ["git", "rev-parse", "origin/master"],
                capture_output=True, text=True, cwd=repo_path, check=True,
            )
        current_sha = remote_sha.stdout.strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not determine remote HEAD: {e}")

    indexed_sha = repo.get("indexed_commit_sha")
    commits_behind = 0
    changed_files: list[str] = []

    if indexed_sha and indexed_sha != current_sha:
        try:
            commits_behind = count_commits_between(repo_path, indexed_sha, current_sha)
            changed_files = get_changed_files(repo_path, indexed_sha, current_sha)
        except GitError:
            # If the indexed SHA doesn't exist (e.g. shallow clone), count is unknown
            commits_behind = -1
    elif not indexed_sha:
        # Never indexed — get current HEAD
        current_sha = get_head_sha(repo_path)

    store.update_repo(
        repo_id,
        current_commit_sha=current_sha,
        commits_behind=commits_behind,
    )

    return {
        "indexed_sha": indexed_sha,
        "current_sha": current_sha,
        "commits_behind": commits_behind,
        "changed_files": changed_files,
    }


@router.post("/repos/{repo_id}/sync")
def sync_repo(repo_id: int):
    """Pull latest changes and re-index changed files."""
    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")
    repo = store.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repo not found")

    if _task_manager.has_running_task():
        raise HTTPException(status_code=409, detail="A task is already running")

    from indiseek.storage.sqlite_store import SqliteStore

    repo_path = Path(repo["local_path"])
    indexed_sha = repo.get("indexed_commit_sha")

    def _run():
        from indiseek.git_utils import get_changed_files, get_head_sha, pull_remote
        from indiseek.indexer.pipeline import run_lexical, run_treesitter

        progress = _make_progress_callback(task_id)
        s = SqliteStore(config.SQLITE_PATH)
        s.init_db()

        # Step 1: Pull
        progress({"step": "sync", "status": "pulling"})
        try:
            pull_remote(repo_path)
        except GitError as e:
            raise RuntimeError(f"git pull failed: {e}") from e

        new_sha = get_head_sha(repo_path)

        # Check if there are actual changes
        if indexed_sha and indexed_sha == new_sha:
            s.update_repo(
                repo_id,
                current_commit_sha=new_sha,
                commits_behind=0,
            )
            return {"status": "up_to_date", "sha": new_sha}

        # Step 2: Get changed files
        changed: list[str] = []
        if indexed_sha:
            try:
                changed = get_changed_files(repo_path, indexed_sha, new_sha)
            except GitError:
                changed = []  # full re-index on failure

        progress({"step": "sync", "status": "indexing", "changed_files": len(changed)})

        # Step 3: Re-index changed files (or full if no indexed_sha)
        if changed:
            # Delete old data for changed files and re-parse them
            for fp in changed:
                s.clear_index_data_for_prefix(fp, repo_id=repo_id)
            # Re-run tree-sitter on the changed files
            from indiseek.indexer.parser import TypeScriptParser
            ts_parser = TypeScriptParser()
            for fp in changed:
                fpath = repo_path / fp
                if not fpath.exists() or fpath.suffix not in (".ts", ".tsx"):
                    continue
                try:
                    symbols = ts_parser.parse_file(fpath, fp)
                    chunks = ts_parser.chunk_file(fpath, fp)
                    if symbols:
                        s.insert_symbols(symbols, repo_id=repo_id)
                    if chunks:
                        s.insert_chunks(chunks, repo_id=repo_id)
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                    s.insert_file_content(fp, content, repo_id=repo_id)
                except Exception as e:
                    logger.warning("Failed to re-parse %s: %s", fp, e)

            # Delete rows for deleted files
            for fp in changed:
                fpath = repo_path / fp
                if not fpath.exists():
                    s.clear_index_data_for_prefix(fp, repo_id=repo_id)
        else:
            # Full re-index
            run_treesitter(s, repo_path, on_progress=progress, repo_id=repo_id)

        # Step 4: Rebuild lexical index (always full rebuild)
        progress({"step": "sync", "status": "lexical"})
        tantivy_path = config.get_tantivy_path(repo_id)
        run_lexical(s, tantivy_path, on_progress=progress, repo_id=repo_id)

        # Step 5: Update repo metadata
        now = datetime.now(timezone.utc).isoformat()
        s.update_repo(
            repo_id,
            indexed_commit_sha=new_sha,
            current_commit_sha=new_sha,
            commits_behind=0,
            last_indexed_at=now,
        )
        s.set_metadata("last_index_at", now)

        return {"status": "synced", "sha": new_sha, "changed_files": len(changed)}

    task_id = _task_manager.submit("sync", _run)
    return {"task_id": task_id, "name": "sync", "status": "running"}


# ── Stats ──


@router.get("/stats")
def get_stats(repo_id: int = Query(1)):
    """Pipeline coverage statistics across all three stores."""
    result: dict[str, Any] = {
        "sqlite": {"available": False},
        "lancedb": {"available": False},
        "tantivy": {"available": False},
    }

    store = _get_sqlite_store()
    if store:
        try:
            result["sqlite"] = {
                "available": True,
                "files_parsed": len(store.get_all_file_paths_from_chunks(repo_id=repo_id)),
                "chunks": store.count("chunks", repo_id=repo_id),
                "symbols": store.count("symbols", repo_id=repo_id),
                "scip_symbols": store.count("scip_symbols", repo_id=repo_id),
                "scip_occurrences": store.count("scip_occurrences", repo_id=repo_id),
                "file_summaries": store.count("file_summaries", repo_id=repo_id),
            }
        except Exception as e:
            logger.warning("Error reading SQLite stats: %s", e)
            result["sqlite"] = {"available": True, "error": str(e)}

    vs = _get_vector_store(repo_id=repo_id)
    if vs:
        try:
            result["lancedb"] = {
                "available": True,
                "embedded_chunks": vs.count(),
            }
        except Exception:
            result["lancedb"] = {"available": True, "embedded_chunks": 0}

    if store:
        lexical = _get_lexical_indexer(store, repo_id=repo_id)
        if lexical:
            try:
                result["tantivy"] = {
                    "available": True,
                    "indexed_docs": lexical.doc_count(),
                }
            except Exception:
                result["tantivy"] = {"available": True, "indexed_docs": 0}

    return result


# ── Tree ──


@router.get("/tree")
def get_tree(path: str = "", repo_id: int = Query(1)):
    """One level of directory tree with coverage counts."""
    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")

    repo_path = config.get_repo_path(repo_id)
    if not repo_path or not repo_path.is_dir():
        raise HTTPException(status_code=503, detail="Repo path not configured or missing")

    # Get all git-tracked files
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, cwd=repo_path, check=True,
        )
        all_files = [f for f in result.stdout.strip().split("\n") if f]
    except Exception:
        all_files = []

    # Get coverage data
    chunked_files = store.get_all_file_paths_from_chunks(repo_id=repo_id)
    summarized_files = store.get_all_file_paths_from_summaries(repo_id=repo_id)

    vs = _get_vector_store(repo_id=repo_id)
    embedded_files: set[str] = set()
    if vs:
        try:
            arrow_table = vs._get_table().to_arrow()
            embedded_files = set(arrow_table.column("file_path").to_pylist())
        except Exception:
            pass

    # Filter to path prefix
    prefix = path.rstrip("/") + "/" if path else ""
    if prefix:
        files_under = [f for f in all_files if f.startswith(prefix)]
    else:
        files_under = all_files

    # Build one level of children
    children: dict[str, dict] = {}
    for f in files_under:
        # Strip prefix to get relative
        rel = f[len(prefix):] if prefix else f
        parts = rel.split("/")

        if len(parts) == 1:
            # Direct file child
            children[parts[0]] = {
                "name": parts[0],
                "type": "file",
                "parsed": f in chunked_files,
                "summarized": f in summarized_files,
                "embedded": f in embedded_files,
            }
        else:
            # Directory child — aggregate stats
            dirname = parts[0]
            if dirname not in children:
                children[dirname] = {
                    "name": dirname,
                    "type": "directory",
                    "total_files": 0,
                    "parsed": 0,
                    "summarized": 0,
                    "embedded": 0,
                }
            children[dirname]["total_files"] += 1
            if f in chunked_files:
                children[dirname]["parsed"] += 1
            if f in summarized_files:
                children[dirname]["summarized"] += 1
            if f in embedded_files:
                children[dirname]["embedded"] += 1

    # Batch-fetch summaries for file and directory children
    file_children = [
        (prefix or "") + c["name"] for c in children.values() if c["type"] == "file"
    ]
    dir_children = [
        ((prefix or "") + c["name"]).rstrip("/") for c in children.values() if c["type"] == "directory"
    ]

    file_summary_map: dict[str, str] = {}
    if file_children:
        for fp in file_children:
            row = store.get_file_summary(fp, repo_id=repo_id)
            if row:
                file_summary_map[fp] = row["summary"]

    dir_summary_map: dict[str, str] = {}
    if dir_children:
        dir_summary_map = store.get_directory_summaries(dir_children, repo_id=repo_id)

    # Attach summaries to children
    for child in children.values():
        full_path = (prefix or "") + child["name"]
        if child["type"] == "file":
            child["summary"] = file_summary_map.get(full_path)
        else:
            child["summary"] = dir_summary_map.get(full_path.rstrip("/"))

    return {
        "path": path,
        "children": sorted(children.values(), key=lambda c: (c["type"] == "file", c["name"])),
    }


# ── File detail ──


@router.get("/files/{file_path:path}")
def get_file_detail(file_path: str, repo_id: int = Query(1)):
    """File summary, chunks, and per-chunk pipeline status."""
    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")

    summary = store.get_file_summary(file_path, repo_id=repo_id)
    chunks = store.get_chunks_by_file(file_path, repo_id=repo_id)
    symbols = store.get_symbols_by_file(file_path, repo_id=repo_id)

    # Check which chunks are embedded
    vs = _get_vector_store(repo_id=repo_id)
    embedded_ids: set[int] = set()
    if vs:
        try:
            embedded_ids = vs.get_chunk_ids()
        except Exception:
            pass

    chunk_list = []
    for c in chunks:
        chunk_list.append({
            **dict(c),
            "embedded": c["id"] in embedded_ids,
        })

    return {
        "file_path": file_path,
        "summary": dict(summary) if summary else None,
        "chunks": chunk_list,
        "symbols": [dict(s) for s in symbols],
    }


# ── Chunk detail ──


@router.get("/chunks/{chunk_id}")
def get_chunk_detail(chunk_id: int, repo_id: int = Query(1)):
    """Full chunk data with pipeline status."""
    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")

    chunk = store.get_chunk_by_id(chunk_id, repo_id=repo_id)
    if not chunk:
        raise HTTPException(status_code=404, detail="Chunk not found")

    vs = _get_vector_store(repo_id=repo_id)
    embedded = False
    if vs:
        try:
            embedded = chunk_id in vs.get_chunk_ids()
        except Exception:
            pass

    return {**chunk, "embedded": embedded}


# ── Search ──


class SearchQuery(BaseModel):
    q: str
    mode: str = "hybrid"
    limit: int = 10


@router.get("/search")
def search_code(
    q: str = Query(...),
    mode: str = Query("hybrid"),
    limit: int = Query(10),
    repo_id: int = Query(1),
):
    """Search code chunks via semantic, lexical, or hybrid modes."""
    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")

    from indiseek.tools.search_code import CodeSearcher

    lexical = _get_lexical_indexer(store, repo_id=repo_id)

    embed_fn = None
    if mode in ("semantic", "hybrid") and config.GEMINI_API_KEY:
        from indiseek.agent.provider import GeminiProvider
        provider = GeminiProvider()
        embed_fn = lambda texts: provider.embed(texts)  # noqa: E731

    try:
        searcher = CodeSearcher(
            lexical_indexer=lexical,
            embed_fn=embed_fn,
        )
        results = searcher.search(q, mode=mode, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "query": q,
        "mode": mode,
        "results": [
            {
                "chunk_id": r.chunk_id,
                "file_path": r.file_path,
                "symbol_name": r.symbol_name,
                "chunk_type": r.chunk_type,
                "content": r.content[:500],
                "score": r.score,
                "match_type": r.match_type,
            }
            for r in results
        ],
    }


# ── Indexing operations ──


class QueryRequest(BaseModel):
    prompt: str
    force: bool = False
    repo_id: int = 1
    mode: str = "auto"  # "auto", "single", "multi"


class RunRequest(BaseModel):
    path_filter: str | None = None
    repo_id: int = 1


class RunScipRequest(BaseModel):
    scip_path: str | None = None
    repo_id: int = 1


def _make_progress_callback(task_id: str):
    """Create a progress callback bound to a task ID."""
    def callback(event: dict):
        _task_manager.push_progress(task_id, event)
    return callback


@router.post("/run/treesitter")
def run_treesitter_op(req: RunRequest = RunRequest()):
    """Trigger tree-sitter parsing in background."""
    if _task_manager.has_running_task():
        raise HTTPException(status_code=409, detail="A task is already running")

    from indiseek.indexer.pipeline import run_treesitter
    from indiseek.storage.sqlite_store import SqliteStore

    repo_path = config.get_repo_path(req.repo_id)

    def _run(path_filter=None):
        store = SqliteStore(config.SQLITE_PATH)
        store.init_db()
        result = run_treesitter(
            store, repo_path, path_filter=path_filter,
            on_progress=_make_progress_callback(task_id),
            repo_id=req.repo_id,
        )
        store.set_metadata("last_index_at", datetime.now(timezone.utc).isoformat())
        return result

    task_id = _task_manager.submit("treesitter", _run, path_filter=req.path_filter)
    return {"task_id": task_id, "name": "treesitter", "status": "running"}


@router.post("/run/scip")
def run_scip_op(req: RunScipRequest = RunScipRequest()):
    """Trigger SCIP loading in background."""
    if _task_manager.has_running_task():
        raise HTTPException(status_code=409, detail="A task is already running")

    from indiseek.indexer.pipeline import run_scip
    from indiseek.storage.sqlite_store import SqliteStore

    repo_path = config.get_repo_path(req.repo_id)
    scip_path = Path(req.scip_path) if req.scip_path else repo_path / "index.scip"

    def _run():
        store = SqliteStore(config.SQLITE_PATH)
        store.init_db()
        result = run_scip(
            store, scip_path,
            on_progress=_make_progress_callback(task_id),
            repo_id=req.repo_id,
        )
        store.set_metadata("last_index_at", datetime.now(timezone.utc).isoformat())
        return result

    task_id = _task_manager.submit("scip", _run)
    return {"task_id": task_id, "name": "scip", "status": "running"}


@router.post("/run/embed")
def run_embed_op(req: RunRequest = RunRequest()):
    """Trigger embedding in background."""
    if _task_manager.has_running_task():
        raise HTTPException(status_code=409, detail="A task is already running")

    if not config.GEMINI_API_KEY:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY not configured")

    from indiseek.indexer.embedder import Embedder
    from indiseek.storage.sqlite_store import SqliteStore
    from indiseek.storage.vector_store import VectorStore

    table_name = config.get_lancedb_table_name(req.repo_id)

    def _run(path_filter=None):
        store = SqliteStore(config.SQLITE_PATH)
        store.init_db()
        vs = VectorStore(config.LANCEDB_PATH, dims=config.EMBEDDING_DIMS, table_name=table_name)
        embedder = Embedder(store, vs)
        result = {"embedded": embedder.embed_all_chunks(
            path_filter=path_filter,
            on_progress=_make_progress_callback(task_id),
            repo_id=req.repo_id,
        )}
        store.set_metadata("last_index_at", datetime.now(timezone.utc).isoformat())
        return result

    task_id = _task_manager.submit("embed", _run, path_filter=req.path_filter)
    return {"task_id": task_id, "name": "embed", "status": "running"}


@router.post("/run/summarize")
def run_summarize_op(req: RunRequest = RunRequest()):
    """Trigger file summarization in background."""
    if _task_manager.has_running_task():
        raise HTTPException(status_code=409, detail="A task is already running")

    if not config.GEMINI_API_KEY:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY not configured")

    from indiseek.indexer.summarizer import Summarizer
    from indiseek.storage.sqlite_store import SqliteStore

    repo_path = config.get_repo_path(req.repo_id)

    def _run(path_filter=None):
        store = SqliteStore(config.SQLITE_PATH)
        store.init_db()
        summarizer = Summarizer(store, repo_id=req.repo_id)
        result = {"summarized": summarizer.summarize_repo(
            repo_path, path_filter=path_filter,
            on_progress=_make_progress_callback(task_id),
        )}
        store.set_metadata("last_index_at", datetime.now(timezone.utc).isoformat())
        return result

    task_id = _task_manager.submit("summarize", _run, path_filter=req.path_filter)
    return {"task_id": task_id, "name": "summarize", "status": "running"}


@router.post("/run/summarize-dirs")
def run_summarize_dirs_op(req: RunRequest = RunRequest()):
    """Trigger directory summarization in background."""
    if _task_manager.has_running_task():
        raise HTTPException(status_code=409, detail="A task is already running")

    if not config.GEMINI_API_KEY:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY not configured")

    from indiseek.indexer.pipeline import run_summarize_dirs
    from indiseek.storage.sqlite_store import SqliteStore

    def _run():
        store = SqliteStore(config.SQLITE_PATH)
        store.init_db()
        result = run_summarize_dirs(
            store,
            on_progress=_make_progress_callback(task_id),
            repo_id=req.repo_id,
        )
        store.set_metadata("last_index_at", datetime.now(timezone.utc).isoformat())
        return result

    task_id = _task_manager.submit("summarize-dirs", _run)
    return {"task_id": task_id, "name": "summarize-dirs", "status": "running"}


@router.post("/run/lexical")
def run_lexical_op(req: RunRequest = RunRequest()):
    """Trigger lexical index build in background."""
    if _task_manager.has_running_task():
        raise HTTPException(status_code=409, detail="A task is already running")

    from indiseek.indexer.pipeline import run_lexical
    from indiseek.storage.sqlite_store import SqliteStore

    tantivy_path = config.get_tantivy_path(req.repo_id)

    def _run():
        store = SqliteStore(config.SQLITE_PATH)
        store.init_db()
        result = run_lexical(
            store, tantivy_path,
            on_progress=_make_progress_callback(task_id),
            repo_id=req.repo_id,
        )
        store.set_metadata("last_index_at", datetime.now(timezone.utc).isoformat())
        return result

    task_id = _task_manager.submit("lexical", _run)
    return {"task_id": task_id, "name": "lexical", "status": "running"}


@router.post("/run/query")
def run_query_op(req: QueryRequest):
    """Submit a natural language query to the agent loop in background."""
    if not config.GEMINI_API_KEY:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY not configured")

    from indiseek.storage.sqlite_store import SqliteStore

    prompt = req.prompt
    store = SqliteStore(config.SQLITE_PATH)
    store.init_db()

    # Cache check (skip if force=True)
    if not req.force:
        from indiseek.tools.search_code import compute_query_similarity

        last_index_at = store.get_metadata("last_index_at")
        candidates = store.get_completed_queries_since(last_index_at, repo_id=req.repo_id)
        best_match = None
        best_sim = 0.0
        for cand in candidates:
            sim = compute_query_similarity(prompt, cand["prompt"])
            if sim >= 0.8 and sim > best_sim:
                best_sim = sim
                best_match = cand
        if best_match:
            evidence_json = best_match["evidence"] or "[]"
            new_id = store.insert_cached_query(
                prompt, best_match["answer"] or "",
                evidence_json, best_match["id"],
                best_match["duration_secs"] or 0.0,
                repo_id=req.repo_id,
            )
            try:
                evidence = json.loads(evidence_json)
            except (json.JSONDecodeError, TypeError):
                evidence = []
            logger.info(
                "Cache hit: query %d matched query %d (sim=%.2f)",
                new_id, best_match["id"], best_sim,
            )
            return {
                "cached": True,
                "query_id": new_id,
                "source_query_id": best_match["id"],
                "answer": best_match["answer"],
                "evidence": evidence,
            }

    if _task_manager.has_running_task():
        raise HTTPException(status_code=409, detail="A task is already running")

    strategy_name = _resolve_strategy_name(prompt, req.mode)
    available = strategy_registry.list_strategies()
    if strategy_name not in available:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy {strategy_name!r}. Available: {', '.join(available)}",
        )

    # Persist query in SQLite
    query_id = store.insert_query(prompt, repo_id=req.repo_id)

    def _run():
        import time
        qstore = SqliteStore(config.SQLITE_PATH)
        qstore.init_db()
        start = time.monotonic()
        try:
            strategy = strategy_registry.create(strategy_name, repo_id=req.repo_id)
            result = strategy.run(prompt, on_progress=_make_progress_callback(task_id))
            duration = time.monotonic() - start
            evidence = [
                {"tool": e.tool, "args": e.args, "summary": e.summary}
                for e in result.evidence
            ]
            usage_dict = result.metadata.get("usage", {})
            qstore.complete_query(
                query_id, result.answer, json.dumps(evidence), duration,
                prompt_tokens=usage_dict.get("prompt_tokens"),
                completion_tokens=usage_dict.get("completion_tokens"),
                estimated_cost=usage_dict.get("estimated_cost"),
            )
            return {
                "query_id": query_id,
                "answer": result.answer,
                "evidence": evidence,
                "usage": usage_dict or None,
            }
        except Exception as exc:
            duration = time.monotonic() - start
            qstore.fail_query(query_id, str(exc))
            raise

    task_id = _task_manager.submit("query", _run)
    return {"task_id": task_id, "name": "query", "status": "running", "query_id": query_id}


# ── Query history ──


@router.get("/queries")
def list_queries(repo_id: int = Query(1)):
    """List recent queries (without answer/evidence)."""
    store = _get_sqlite_store()
    if not store:
        return []
    try:
        return store.list_queries(repo_id=repo_id)
    except Exception:
        return []


@router.get("/queries/{query_id}")
def get_query(query_id: int):
    """Get full query detail including answer and evidence."""
    store = _get_sqlite_store()
    if not store:
        raise HTTPException(status_code=503, detail="SQLite store not available")
    row = store.get_query(query_id)
    if not row:
        raise HTTPException(status_code=404, detail="Query not found")
    return row


# ── Task status ──


@router.get("/tasks")
def list_tasks():
    """List all tasks with status."""
    tasks = _task_manager.list_tasks()
    # Strip progress_events from list view (can be large)
    return [
        {k: v for k, v in t.items() if k != "progress_events"}
        for t in tasks
    ]


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    """Task detail with latest progress."""
    task = _task_manager.get_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


# ── SSE streaming ──


@router.get("/tasks/{task_id}/stream")
def stream_task(task_id: str):
    """SSE stream of progress events for a task."""
    task = _task_manager.get_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    sub_queue = _task_manager.subscribe(task_id)
    if sub_queue is None:
        raise HTTPException(status_code=404, detail="Task not found")

    def event_generator():
        # First, replay any existing progress events
        existing = task.get("progress_events", [])
        for evt in existing:
            yield f"data: {json.dumps({'type': 'progress', **evt})}\n\n"

        # If task already finished, send final event
        if task["status"] in ("completed", "failed"):
            if task["status"] == "completed":
                yield f"data: {json.dumps({'type': 'done', 'result': task.get('result')})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'error', 'error': task.get('error', '')})}\n\n"
            return

        # Stream new events from queue
        while True:
            try:
                event = sub_queue.get(timeout=30)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ("done", "error"):
                    break
            except queue.Empty:
                # Send keepalive
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
