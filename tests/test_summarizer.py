"""Tests for file summarizer and SQLite file summary storage."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from indiseek.storage.sqlite_store import SqliteStore


# ── Fixtures ──


@pytest.fixture
def store(tmp_path: Path) -> SqliteStore:
    s = SqliteStore(tmp_path / "test.db")
    s.init_db()
    return s


def _make_mock_provider(summary: str = "Does something useful.") -> MagicMock:
    provider = MagicMock()
    provider.generate = MagicMock(return_value=summary)
    return provider


# ── SqliteStore file summary tests ──


class TestFileSummaryStorage:
    def test_insert_and_retrieve(self, store: SqliteStore) -> None:
        store.insert_file_summary("src/main.ts", "Entry point for the app.", "ts", 100)
        summaries = store.get_file_summaries()
        assert len(summaries) == 1
        assert summaries[0]["file_path"] == "src/main.ts"
        assert summaries[0]["summary"] == "Entry point for the app."
        assert summaries[0]["language"] == "ts"
        assert summaries[0]["line_count"] == 100

    def test_upsert_replaces(self, store: SqliteStore) -> None:
        store.insert_file_summary("a.ts", "Old summary.", "ts", 10)
        store.insert_file_summary("a.ts", "New summary.", "ts", 12)
        summaries = store.get_file_summaries()
        assert len(summaries) == 1
        assert summaries[0]["summary"] == "New summary."

    def test_batch_insert(self, store: SqliteStore) -> None:
        store.insert_file_summaries([
            ("a.ts", "File A.", "ts", 10),
            ("b.ts", "File B.", "ts", 20),
            ("c.json", "Config file.", "json", 5),
        ])
        assert store.count("file_summaries") == 3

    def test_get_by_directory(self, store: SqliteStore) -> None:
        store.insert_file_summaries([
            ("src/server/index.ts", "Server entry.", "ts", 50),
            ("src/server/hmr.ts", "HMR handler.", "ts", 100),
            ("src/client/main.ts", "Client entry.", "ts", 30),
        ])
        server_summaries = store.get_file_summaries("src/server")
        assert len(server_summaries) == 2
        assert all("server" in s["file_path"] for s in server_summaries)

    def test_get_all_when_no_directory(self, store: SqliteStore) -> None:
        store.insert_file_summaries([
            ("a.ts", "A.", "ts", 1),
            ("b/c.ts", "C.", "ts", 2),
        ])
        assert len(store.get_file_summaries()) == 2
        assert len(store.get_file_summaries(None)) == 2

    def test_directory_tree(self, store: SqliteStore) -> None:
        store.insert_file_summaries([
            ("src/server/index.ts", "Server entry.", "ts", 50),
            ("src/server/hmr.ts", "HMR handler.", "ts", 100),
            ("src/client/main.ts", "Client entry.", "ts", 30),
            ("package.json", "Package config.", "json", 20),
        ])
        tree = store.get_directory_tree()
        assert tree["package.json"] == "Package config."
        assert tree["src"]["server"]["index.ts"] == "Server entry."
        assert tree["src"]["server"]["hmr.ts"] == "HMR handler."
        assert tree["src"]["client"]["main.ts"] == "Client entry."

    def test_directory_tree_empty(self, store: SqliteStore) -> None:
        tree = store.get_directory_tree()
        assert tree == {}


# ── Summarizer tests ──


class TestSummarizer:
    def test_summarize_file(self) -> None:
        from indiseek.indexer.summarizer import Summarizer

        provider = _make_mock_provider("Exports the main server factory function.")
        store_mock = MagicMock()
        summarizer = Summarizer(store_mock, provider=provider)

        result = summarizer.summarize_file("src/server.ts", "export function createServer() {}")
        assert result == "Exports the main server factory function."
        provider.generate.assert_called_once()

    def test_summarize_file_truncates_large_content(self) -> None:
        from indiseek.indexer.summarizer import Summarizer

        provider = _make_mock_provider("Large file summary.")
        store_mock = MagicMock()
        summarizer = Summarizer(store_mock, provider=provider)

        large_content = "x" * 50_000
        summarizer.summarize_file("big.ts", large_content)

        # Check that the prompt passed to generate was truncated
        call_args = provider.generate.call_args
        prompt = call_args[0][0]
        assert "... (truncated)" in prompt

    def test_summarize_repo_stores_results(self, tmp_path: Path, store: SqliteStore) -> None:
        from indiseek.indexer.summarizer import Summarizer

        # Create a mini repo with git
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()  # fake git dir for os.walk fallback

        (repo / "index.ts").write_text("export function main() {}")
        (repo / "utils.ts").write_text("export function helper() {}")
        sub = repo / "src"
        sub.mkdir()
        (sub / "server.ts").write_text("export function serve() {}")

        provider = _make_mock_provider("Does something.")
        summarizer = Summarizer(store, provider=provider, delay=0)

        count = summarizer.summarize_repo(repo)
        assert count == 3
        assert store.count("file_summaries") == 3

    def test_summarize_repo_skips_node_modules(self, tmp_path: Path, store: SqliteStore) -> None:
        from indiseek.indexer.summarizer import Summarizer

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "index.ts").write_text("main code")
        nm = repo / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "dep.ts").write_text("dependency code")

        provider = _make_mock_provider("Summary.")
        summarizer = Summarizer(store, provider=provider, delay=0)
        count = summarizer.summarize_repo(repo)

        assert count == 1  # only index.ts, not node_modules

    def test_summarize_repo_handles_api_errors(self, tmp_path: Path, store: SqliteStore) -> None:
        from indiseek.indexer.summarizer import Summarizer

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.ts").write_text("code a")
        (repo / "b.ts").write_text("code b")

        call_count = 0

        def flaky_generate(prompt: str, system: str | None = None) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Transient API error")
            return "Summary of file."

        provider = MagicMock()
        provider.generate = MagicMock(side_effect=flaky_generate)
        summarizer = Summarizer(store, provider=provider, delay=0)

        count = summarizer.summarize_repo(repo)
        # One file fails, one succeeds
        assert count == 1

    def test_summarize_repo_aborts_on_auth_error(self, tmp_path: Path, store: SqliteStore) -> None:
        from indiseek.indexer.summarizer import Summarizer

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "a.ts").write_text("code")

        provider = MagicMock()
        provider.generate = MagicMock(side_effect=RuntimeError("API_KEY_INVALID"))
        summarizer = Summarizer(store, provider=provider, delay=0)

        with pytest.raises(RuntimeError, match="API key error"):
            summarizer.summarize_repo(repo)

    def test_summarize_repo_empty(self, tmp_path: Path, store: SqliteStore) -> None:
        from indiseek.indexer.summarizer import Summarizer

        repo = tmp_path / "repo"
        repo.mkdir()

        provider = _make_mock_provider("Summary.")
        summarizer = Summarizer(store, provider=provider, delay=0)
        count = summarizer.summarize_repo(repo)
        assert count == 0


# ── GeminiProvider.generate test (construction only, no API) ──


class TestGeminiProviderGenerate:
    def test_provider_has_generate_method(self) -> None:
        from indiseek.agent.provider import GeminiProvider
        from indiseek import config

        provider = GeminiProvider(api_key="test-key")
        assert hasattr(provider, "generate")
        assert provider._generation_model == config.GEMINI_MODEL

    def test_provider_custom_generation_model(self) -> None:
        from indiseek.agent.provider import GeminiProvider

        provider = GeminiProvider(api_key="test-key", generation_model="gemini-pro")
        assert provider._generation_model == "gemini-pro"


# ── Directory summary storage tests ──


class TestDirectorySummaryStorage:
    def test_insert_and_retrieve(self, store: SqliteStore) -> None:
        store.insert_directory_summary("src/server", "Server-side HTTP handlers.")
        result = store.get_directory_summary("src/server")
        assert result is not None
        assert result["dir_path"] == "src/server"
        assert result["summary"] == "Server-side HTTP handlers."

    def test_upsert_replaces(self, store: SqliteStore) -> None:
        store.insert_directory_summary("src", "Old summary.")
        store.insert_directory_summary("src", "New summary.")
        result = store.get_directory_summary("src")
        assert result["summary"] == "New summary."

    def test_missing_returns_none(self, store: SqliteStore) -> None:
        result = store.get_directory_summary("nonexistent")
        assert result is None

    def test_batch_insert(self, store: SqliteStore) -> None:
        store.insert_directory_summaries([
            ("src", "Source code root."),
            ("src/server", "Server modules."),
            ("src/client", "Client modules."),
        ])
        assert store.count("directory_summaries") == 3

    def test_batch_lookup(self, store: SqliteStore) -> None:
        store.insert_directory_summaries([
            ("src", "Source root."),
            ("src/server", "Server."),
            ("lib", "Library."),
        ])
        result = store.get_directory_summaries(["src", "lib", "missing"])
        assert result == {"src": "Source root.", "lib": "Library."}

    def test_batch_lookup_empty_list(self, store: SqliteStore) -> None:
        result = store.get_directory_summaries([])
        assert result == {}

    def test_get_all_directory_paths(self, store: SqliteStore) -> None:
        store.insert_directory_summaries([
            ("src", "Source."),
            ("src/server", "Server."),
        ])
        paths = store.get_all_directory_paths_from_summaries()
        assert paths == {"src", "src/server"}

    def test_get_all_directory_paths_empty(self, store: SqliteStore) -> None:
        paths = store.get_all_directory_paths_from_summaries()
        assert paths == set()


# ── Directory summarizer tests ──


class TestSummarizeDirectories:
    def test_summarize_directories_basic(self, store: SqliteStore) -> None:
        """Bottom-up summarization with a simple tree: root has two files, one subdir."""
        from indiseek.indexer.summarizer import Summarizer

        # Seed file summaries
        store.insert_file_summaries([
            ("src/server/index.ts", "Server entry point.", "ts", 50),
            ("src/server/hmr.ts", "HMR handler.", "ts", 100),
            ("src/client/main.ts", "Client entry point.", "ts", 30),
            ("readme.md", "Project documentation.", "markdown", 10),
        ])

        call_count = 0
        def mock_generate(prompt: str, system: str | None = None) -> str:
            nonlocal call_count
            call_count += 1
            # Return a summary that includes the directory name from the prompt
            # The prompt starts with "Directory: <path>/\n"
            dir_line = prompt.split("\n")[0]
            dir_name = dir_line.replace("Directory: ", "").rstrip("/")
            return f"Summary of {dir_name}."

        provider = MagicMock()
        provider.generate = MagicMock(side_effect=mock_generate)
        summarizer = Summarizer(store, provider=provider, delay=0)

        count = summarizer.summarize_directories()

        # Should summarize: src/server, src/client, src, . (root)
        assert count == 4
        assert store.count("directory_summaries") == 4

        # Deepest dirs processed first
        assert store.get_directory_summary("src/server") is not None
        assert store.get_directory_summary("src/client") is not None
        assert store.get_directory_summary("src") is not None
        assert store.get_directory_summary(".") is not None

    def test_summarize_directories_skips_existing(self, store: SqliteStore) -> None:
        """Already-summarized directories are skipped (resume-safe)."""
        from indiseek.indexer.summarizer import Summarizer

        store.insert_file_summaries([
            ("src/a.ts", "File A.", "ts", 10),
            ("src/b.ts", "File B.", "ts", 20),
        ])
        # Pre-insert one directory summary
        store.insert_directory_summary("src", "Already done.")

        provider = _make_mock_provider("New summary.")
        summarizer = Summarizer(store, provider=provider, delay=0)

        count = summarizer.summarize_directories()
        # Only root "." should be summarized, "src" was skipped
        assert count == 1
        # "src" should still have the old summary
        assert store.get_directory_summary("src")["summary"] == "Already done."

    def test_summarize_directories_no_file_summaries(self, store: SqliteStore) -> None:
        """Returns 0 when there are no file summaries."""
        from indiseek.indexer.summarizer import Summarizer

        provider = _make_mock_provider("Summary.")
        summarizer = Summarizer(store, provider=provider, delay=0)

        count = summarizer.summarize_directories()
        assert count == 0

    def test_summarize_directories_progress_callback(self, store: SqliteStore) -> None:
        """on_progress callback is called for each directory."""
        from indiseek.indexer.summarizer import Summarizer

        store.insert_file_summaries([
            ("a.ts", "Root file.", "ts", 5),
            ("src/b.ts", "Nested file.", "ts", 10),
        ])

        provider = _make_mock_provider("Dir summary.")
        summarizer = Summarizer(store, provider=provider, delay=0)

        progress_events = []
        count = summarizer.summarize_directories(
            on_progress=lambda e: progress_events.append(e)
        )

        assert count == 2  # "src" and "."
        assert len(progress_events) == 2
        assert all(e["step"] == "summarize-dirs" for e in progress_events)

    def test_summarize_directories_uses_child_dir_summaries(self, store: SqliteStore) -> None:
        """Parent directories see child directory summaries in their prompt."""
        from indiseek.indexer.summarizer import Summarizer

        store.insert_file_summaries([
            ("src/server/index.ts", "Server entry.", "ts", 50),
        ])

        prompts_seen: list[str] = []

        def capture_generate(prompt: str, system: str | None = None) -> str:
            prompts_seen.append(prompt)
            return "Summary."

        provider = MagicMock()
        provider.generate = MagicMock(side_effect=capture_generate)
        summarizer = Summarizer(store, provider=provider, delay=0)

        summarizer.summarize_directories()

        # 3 dirs: src/server, src, .
        assert len(prompts_seen) == 3

        # The "src" prompt should mention "server/" as a subdirectory
        src_prompt = [p for p in prompts_seen if p.startswith("Directory: src/\n")][0]
        assert "server/" in src_prompt
        assert "Subdirectories:" in src_prompt
