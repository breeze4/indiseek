"""Git utilities for repository management."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    """Raised when a git operation fails."""


def _run_git(args: list[str], cwd: Path) -> str:
    """Run a git command and return stdout. Raises GitError on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=cwd,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise GitError(f"git {' '.join(args)} failed: {e.stderr.strip()}") from e
    except FileNotFoundError:
        raise GitError("git is not installed or not in PATH")


def get_head_sha(repo_path: Path) -> str:
    """Return the full SHA of HEAD."""
    return _run_git(["rev-parse", "HEAD"], cwd=repo_path)


def fetch_remote(repo_path: Path, remote: str = "origin") -> None:
    """Run git fetch for the given remote."""
    _run_git(["fetch", remote], cwd=repo_path)


def pull_remote(repo_path: Path, remote: str = "origin") -> None:
    """Run git pull for the given remote."""
    _run_git(["pull", remote], cwd=repo_path)


def count_commits_between(repo_path: Path, from_sha: str, to_sha: str) -> int:
    """Count the number of commits between two SHAs."""
    output = _run_git(["rev-list", "--count", f"{from_sha}..{to_sha}"], cwd=repo_path)
    return int(output)


def get_changed_files(repo_path: Path, from_sha: str, to_sha: str) -> list[str]:
    """Return list of files changed between two SHAs."""
    output = _run_git(["diff", "--name-only", f"{from_sha}..{to_sha}"], cwd=repo_path)
    if not output:
        return []
    return output.split("\n")


def clone_repo(url: str, dest: Path, shallow: bool = True) -> None:
    """Clone a repository to the given destination."""
    args = ["clone"]
    if shallow:
        args.extend(["--depth", "1"])
    args.extend([url, str(dest)])
    try:
        subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise GitError(f"git clone failed: {e.stderr.strip()}") from e
    except FileNotFoundError:
        raise GitError("git is not installed or not in PATH")
