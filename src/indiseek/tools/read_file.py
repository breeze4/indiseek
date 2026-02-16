"""read_file tool — read source code from the indexed repository."""

from __future__ import annotations

from pathlib import Path

DEFAULT_LINE_CAP = 200


def read_file(
    repo_path: Path,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """Read source code from the repository with line numbers.

    Args:
        repo_path: Root path of the indexed repository.
        path: Relative file path within the repo.
        start_line: Optional first line to read (1-based, inclusive).
        end_line: Optional last line to read (1-based, inclusive).

    Returns:
        File content with line numbers, or an error message.
    """
    # Resolve and validate the path is within the repo
    full_path = (repo_path / path).resolve()
    repo_resolved = repo_path.resolve()

    if not str(full_path).startswith(str(repo_resolved)):
        return f"Error: Path '{path}' is outside the repository."

    if not full_path.exists():
        return f"Error: File '{path}' not found."

    if not full_path.is_file():
        return f"Error: '{path}' is not a file."

    try:
        content = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error reading '{path}': {e}"

    lines = content.splitlines()

    # Apply line range
    total_lines = len(lines)
    truncated = False
    if start_line is not None or end_line is not None:
        s = (start_line or 1) - 1  # Convert to 0-based
        e = end_line or len(lines)
        s = max(0, s)
        e = min(len(lines), e)
        selected = lines[s:e]
        line_offset = s + 1
    else:
        selected = lines[:DEFAULT_LINE_CAP]
        line_offset = 1
        if total_lines > DEFAULT_LINE_CAP:
            truncated = True

    # Format with line numbers
    numbered = []
    for i, line in enumerate(selected):
        numbered.append(f"{line_offset + i:>6} | {line}")

    header = f"File: {path}"
    if start_line or end_line:
        header += f" (lines {line_offset}-{line_offset + len(selected) - 1})"
    header += f"\n{'─' * 60}"

    result = header + "\n" + "\n".join(numbered)

    if truncated:
        result += (
            f"\n... (showing first {DEFAULT_LINE_CAP} of {total_lines} lines."
            " Use start_line/end_line to read more.)"
        )

    return result
