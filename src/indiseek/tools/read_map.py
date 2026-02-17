"""read_map tool — returns directory structure with file summaries."""

from __future__ import annotations

from indiseek.storage.sqlite_store import SqliteStore


def _format_tree(
    tree: dict,
    prefix: str = "",
    is_root: bool = True,
    dir_summaries: dict[str, str] | None = None,
    current_path: str = "",
) -> str:
    """Recursively format a nested dict as a readable tree with summaries."""
    lines: list[str] = []
    items = sorted(tree.items())
    for i, (name, value) in enumerate(items):
        is_last = i == len(items) - 1
        connector = "" if is_root else ("└── " if is_last else "├── ")
        child_prefix = "" if is_root else (prefix + ("    " if is_last else "│   "))

        if isinstance(value, dict):
            dir_path = f"{current_path}/{name}".lstrip("/") if current_path else name
            summary = dir_summaries.get(dir_path) if dir_summaries else None
            if summary:
                lines.append(f"{prefix}{connector}{name}/ — {summary}")
            else:
                lines.append(f"{prefix}{connector}{name}/")
            lines.append(_format_tree(
                value, child_prefix, is_root=False,
                dir_summaries=dir_summaries, current_path=dir_path,
            ))
        else:
            # value is the summary string
            lines.append(f"{prefix}{connector}{name} — {value}")

    return "\n".join(line for line in lines if line)


def read_map(store: SqliteStore, path: str | None = None, repo_id: int = 1) -> str:
    """Return directory structure with file summaries.

    Args:
        store: SQLite store with file_summaries table.
        path: Optional subdirectory to scope results to.
        repo_id: Repository ID to scope results to.

    Returns:
        Formatted tree string with file summaries.
    """
    if path:
        summaries = store.get_file_summaries(directory=path, repo_id=repo_id)
        if not summaries:
            return f"No files found under '{path}'."
        # Build a scoped tree from the summaries
        tree: dict = {}
        for row in summaries:
            parts = row["file_path"].split("/")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = row["summary"]
        header = f"Directory: {path}\n\n"
    else:
        tree = store.get_directory_tree(repo_id=repo_id)
        if not tree:
            return "No file summaries available. Run indexing with --summarize first."
        header = "Repository map:\n\n"

    # Fetch directory summaries for annotating directory lines
    dir_paths = list(store.get_all_directory_paths_from_summaries(repo_id=repo_id))
    dir_summaries = store.get_directory_summaries(dir_paths, repo_id=repo_id) if dir_paths else {}

    return header + _format_tree(tree, dir_summaries=dir_summaries)
