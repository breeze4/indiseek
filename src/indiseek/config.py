"""Configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return val


# Paths
REPO_PATH: Path = Path(os.getenv("REPO_PATH", ""))
DATA_DIR: Path = Path(os.getenv("DATA_DIR", "./data"))

# API keys
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# Models
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIMS: int = int(os.getenv("EMBEDDING_DIMS", "768"))

# Server
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# Derived paths
SQLITE_PATH: Path = DATA_DIR / "indiseek.db"
LANCEDB_PATH: Path = DATA_DIR / "lancedb"
TANTIVY_PATH: Path = DATA_DIR / "tantivy"
REPOS_DIR: Path = DATA_DIR / "repos"


def get_repo_path(repo_id: int) -> Path:
    """Return the local filesystem path for a repo's clone.

    Legacy repo (id=1) uses REPO_PATH if set, otherwise falls back to REPOS_DIR/1.
    """
    if repo_id == 1 and REPO_PATH and REPO_PATH != Path(""):
        return REPO_PATH
    return REPOS_DIR / str(repo_id)


def get_lancedb_table_name(repo_id: int) -> str:
    """Return the LanceDB table name for a repo.

    Legacy repo (id=1) uses the original "chunks" table name.
    New repos use "chunks_{repo_id}".
    """
    if repo_id == 1:
        return "chunks"
    return f"chunks_{repo_id}"


def get_tantivy_path(repo_id: int) -> Path:
    """Return the Tantivy index directory for a repo.

    Legacy repo (id=1) uses the original TANTIVY_PATH.
    New repos use DATA_DIR/tantivy_{repo_id}/.
    """
    if repo_id == 1:
        return TANTIVY_PATH
    return DATA_DIR / f"tantivy_{repo_id}"
