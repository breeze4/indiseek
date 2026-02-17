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
