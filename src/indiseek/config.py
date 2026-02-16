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
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
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
