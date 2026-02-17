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

DIR_SYSTEM_PROMPT = (
    "You are a code documentation assistant. "
    "Given the contents of a directory (child files and subdirectories with their summaries), "
    "summarize the directory's overall responsibility in one sentence. "
    "Be specific. Do not start with 'This directory'."
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

    def summarize_directories(
        self,
        on_progress: Callable[[dict], None] | None = None,
    ) -> int:
        """Summarize directories bottom-up using child file and directory summaries.

        Walks all directories that contain summarized files, starting from the
        deepest level. For each directory, collects child file summaries and
        already-computed child directory summaries, then asks the LLM for a
        one-sentence summary.

        Returns the number of directories summarized.
        """
        # Get all file summaries to determine which directories exist
        all_summaries = self._store.get_file_summaries()
        if not all_summaries:
            print("No file summaries found. Run file summarization first.")
            return 0

        # Build a mapping: dir_path -> list of (filename, summary)
        dir_files: dict[str, list[tuple[str, str]]] = {}
        all_dirs: set[str] = set()
        for row in all_summaries:
            parts = row["file_path"].split("/")
            # Register all ancestor directories
            for i in range(1, len(parts)):
                dir_path = "/".join(parts[:i])
                all_dirs.add(dir_path)
            # Map file to its immediate parent directory
            if len(parts) > 1:
                parent = "/".join(parts[:-1])
            else:
                parent = "."
            dir_files.setdefault(parent, []).append((parts[-1], row["summary"]))
        # Root dir "." always exists if there are any files
        all_dirs.add(".")

        # Skip directories already summarized (resume-safe)
        existing = self._store.get_all_directory_paths_from_summaries()
        dirs_to_process = sorted(all_dirs - existing, key=lambda d: -d.count("/"))

        total = len(dirs_to_process)
        if total == 0:
            print("All directories already summarized.")
            return 0

        print(f"Summarizing {total} directories...")
        summarized = 0
        consecutive_errors = 0
        # Accumulate computed summaries for use by parent directories
        dir_summary_cache: dict[str, str] = {}
        # Load existing summaries into cache for parents that depend on them
        for dp in existing:
            row = self._store.get_directory_summary(dp)
            if row:
                dir_summary_cache[dp] = row["summary"]

        for i, dir_path in enumerate(dirs_to_process, 1):
            # Collect child file summaries
            child_files = dir_files.get(dir_path, [])

            # Collect child directory summaries (immediate children only)
            child_dirs = []
            for d in all_dirs | existing:
                if d == dir_path:
                    continue
                # Check if d is an immediate child of dir_path
                if dir_path == ".":
                    # Immediate children of root: dirs with no "/" in their name
                    if "/" not in d and d in dir_summary_cache:
                        child_dirs.append((d, dir_summary_cache[d]))
                else:
                    # d must start with dir_path/ and have no further /
                    if d.startswith(dir_path + "/"):
                        remainder = d[len(dir_path) + 1:]
                        if "/" not in remainder and d in dir_summary_cache:
                            child_dirs.append((remainder, dir_summary_cache[d]))

            # Build prompt
            lines = [f"Directory: {dir_path}/\n"]
            if child_files:
                lines.append("Files:")
                for fname, summary in sorted(child_files):
                    lines.append(f"  {fname} — {summary}")
            if child_dirs:
                lines.append("Subdirectories:")
                for dname, summary in sorted(child_dirs):
                    lines.append(f"  {dname}/ — {summary}")

            prompt = "\n".join(lines)

            try:
                summary = self._provider.generate(prompt, system=DIR_SYSTEM_PROMPT).strip()
            except Exception as e:
                err_str = str(e)
                if "API_KEY_INVALID" in err_str or "PERMISSION_DENIED" in err_str:
                    raise RuntimeError(f"API key error — aborting: {e}") from e
                print(f"  Error summarizing {dir_path}/: {e}", file=sys.stderr)
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    raise RuntimeError(
                        f"5 consecutive failures — aborting. Last error: {e}"
                    ) from e
                continue

            consecutive_errors = 0
            self._store.insert_directory_summary(dir_path, summary)
            dir_summary_cache[dir_path] = summary
            summarized += 1

            if on_progress:
                on_progress({
                    "step": "summarize-dirs", "current": i, "total": total,
                    "dir": dir_path,
                })

            if i % 20 == 0 or i == total:
                print(f"  {i}/{total} directories processed ({summarized} summarized)")

            if self._delay > 0:
                time.sleep(self._delay)

        print(f"Directory summarization complete: {summarized} directories summarized")
        return summarized

    def _get_summarized_paths(self) -> set[str]:
        """Return file paths that already have summaries in SQLite."""
        return self._store.get_all_file_paths_from_summaries()

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
