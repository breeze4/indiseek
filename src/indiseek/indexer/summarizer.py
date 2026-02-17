"""LLM-summarize each file in the repo to build a navigable map."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from indiseek.agent.provider import GenerationProvider, GeminiProvider
from indiseek.storage.sqlite_store import SqliteStore

SYSTEM_PROMPT = (
    "You are a code documentation assistant. "
    "Given a source file, summarize its responsibility in one sentence. "
    "Be specific about what the code does, not generic. "
    "Do not start with 'This file'. Just state what it does."
)

# Directories to skip when walking the repo
SKIP_DIRS = {
    "node_modules", "dist", ".git", ".svn", "__pycache__",
    ".venv", "vendor", "coverage", ".nyc_output", ".turbo",
    "build", ".cache", ".output",
    "__tests__", "__tests_dts__", "test", "tests", "__test__",
}

# File extensions to include for summarization
SOURCE_EXTENSIONS = {".ts", ".tsx"}


def _detect_language(path: str) -> str | None:
    """Detect language from file extension."""
    ext = os.path.splitext(path)[1].lower()
    lang_map = {
        ".ts": "ts", ".tsx": "tsx", ".js": "js", ".jsx": "jsx",
        ".mjs": "js", ".cjs": "js", ".json": "json",
        ".md": "markdown", ".yaml": "yaml", ".yml": "yaml",
    }
    return lang_map.get(ext)


def _count_lines(content: str) -> int:
    return content.count("\n") + (1 if content and not content.endswith("\n") else 0)


class Summarizer:
    """Summarizes source files using an LLM and stores results in SQLite."""

    def __init__(
        self,
        store: SqliteStore,
        provider: GenerationProvider | None = None,
        delay: float = 0.5,
    ) -> None:
        self._store = store
        self._provider = provider or GeminiProvider()
        self._delay = delay

    def summarize_file(self, file_path: str, content: str) -> str:
        """Summarize a single file's content.

        Args:
            file_path: Relative path for context.
            content: File content.

        Returns:
            One-sentence summary string.
        """
        # Truncate very large files to avoid token limits
        max_chars = 30_000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... (truncated)"

        prompt = f"File: {file_path}\n\n```\n{content}\n```"
        return self._provider.generate(prompt, system=SYSTEM_PROMPT).strip()

    def summarize_repo(
        self,
        repo_path: Path,
        path_filter: str | None = None,
        on_progress: Callable[[dict], None] | None = None,
    ) -> int:
        """Walk all source files in the repo, summarize each, store in SQLite.

        Args:
            repo_path: Root of the repository.
            path_filter: Optional path prefix to restrict which files are summarized.
            on_progress: Optional callback for progress events.

        Returns the number of files summarized.
        """
        source_files = self._get_source_files(repo_path)
        if path_filter:
            source_files = [
                f for f in source_files
                if str(f.relative_to(repo_path)).startswith(path_filter)
            ]

        # Skip files that already have summaries
        existing = self._get_summarized_paths()
        skipped = [f for f in source_files if str(f.relative_to(repo_path)) in existing]
        source_files = [f for f in source_files if str(f.relative_to(repo_path)) not in existing]
        total = len(source_files)

        if skipped:
            print(f"Skipping {len(skipped)} already-summarized files")

        if total == 0:
            print("No new files to summarize.")
            return 0

        print(f"Summarizing {total} files...")
        summarized = 0
        consecutive_errors = 0

        for i, fpath in enumerate(source_files, 1):
            relative = str(fpath.relative_to(repo_path))

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                print(f"  Warning: Cannot read {relative}: {e}", file=sys.stderr)
                continue

            line_count = _count_lines(content)
            language = _detect_language(relative)

            try:
                summary = self.summarize_file(relative, content)
            except Exception as e:
                err_str = str(e)
                if "API_KEY_INVALID" in err_str or "PERMISSION_DENIED" in err_str:
                    raise RuntimeError(f"API key error — aborting: {e}") from e
                print(f"  Error summarizing {relative}: {e}", file=sys.stderr)
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    raise RuntimeError(
                        f"5 consecutive failures — aborting. Last error: {e}"
                    ) from e
                continue

            consecutive_errors = 0
            self._store.insert_file_summary(relative, summary, language, line_count)
            summarized += 1

            if on_progress:
                on_progress({
                    "step": "summarize", "current": i, "total": total,
                    "file": relative,
                })

            if i % 50 == 0 or i == total:
                print(f"  {i}/{total} files processed ({summarized} summarized)")

            if self._delay > 0:
                time.sleep(self._delay)

        print(f"Summarization complete: {summarized} files summarized")
        return summarized

    def _get_summarized_paths(self) -> set[str]:
        """Return file paths that already have summaries in SQLite."""
        cur = self._store._conn.execute("SELECT file_path FROM file_summaries")
        return {row["file_path"] for row in cur.fetchall()}

    def _get_source_files(self, repo_path: Path) -> list[Path]:
        """Get source files to summarize, respecting .gitignore via git ls-files."""
        try:
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
                if not p.exists():
                    continue
                # Skip files in excluded directories
                parts = Path(line).parts
                if any(part in SKIP_DIRS for part in parts):
                    continue
                if p.suffix in SOURCE_EXTENSIONS:
                    files.append(p)
            return sorted(files)
        except (subprocess.CalledProcessError, FileNotFoundError):
            # Fallback: walk directory manually
            return self._walk_source_files(repo_path)

    def _walk_source_files(self, repo_path: Path) -> list[Path]:
        """Fallback: walk directory tree manually."""
        files = []
        for root, dirs, filenames in os.walk(repo_path):
            # Prune skip directories
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for fname in filenames:
                p = Path(root) / fname
                if p.suffix in SOURCE_EXTENSIONS:
                    files.append(p)
        return sorted(files)
