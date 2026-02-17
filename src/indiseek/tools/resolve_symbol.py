"""resolve_symbol tool — look up symbol definitions, references, callers, callees."""

from __future__ import annotations

import logging

from indiseek.storage.sqlite_store import SqliteStore

logger = logging.getLogger(__name__)

VALID_ACTIONS = ("definition", "references", "callers", "callees")


def resolve_symbol(
    store: SqliteStore, symbol_name: str, action: str, repo_id: int = 1
) -> str:
    """Look up a symbol's definition, references, callers, or callees.

    Uses SCIP cross-reference data first, falls back to tree-sitter symbols table.

    Args:
        store: SQLite store with symbols and SCIP tables.
        symbol_name: Name of the symbol to look up.
        action: One of "definition", "references", "callers", "callees".
        repo_id: Repository ID to scope results to.

    Returns:
        Formatted string with file:line locations.
    """
    if action not in VALID_ACTIONS:
        return f"Invalid action '{action}'. Use one of: {', '.join(VALID_ACTIONS)}"

    if action == "definition":
        return _resolve_definition(store, symbol_name, repo_id)
    elif action == "references":
        return _resolve_references(store, symbol_name, repo_id)
    elif action == "callers":
        return _resolve_callers(store, symbol_name, repo_id)
    else:
        return _resolve_callees(store, symbol_name, repo_id)


def _resolve_definition(store: SqliteStore, symbol_name: str, repo_id: int = 1) -> str:
    """Find where a symbol is defined."""
    # Try SCIP first
    scip_defs = store.get_definition(symbol_name, repo_id=repo_id)
    if scip_defs:
        logger.debug("resolve %s/definition: %d SCIP hit(s)", symbol_name, len(scip_defs))
        lines = [f"Definition of '{symbol_name}' (SCIP, {len(scip_defs)} result(s)):"]
        for d in scip_defs:
            lines.append(f"  {d['file_path']}:{d['start_line']}")
        return "\n".join(lines)

    # Fall back to tree-sitter symbols
    ts_syms = store.get_symbols_by_name(symbol_name, repo_id=repo_id)
    if ts_syms:
        logger.debug("resolve %s/definition: %d tree-sitter hit(s) (SCIP miss)", symbol_name, len(ts_syms))
        lines = [f"Definition of '{symbol_name}' (tree-sitter, {len(ts_syms)} result(s)):"]
        for s in ts_syms:
            lines.append(f"  {s['file_path']}:{s['start_line']} ({s['kind']})")
        return "\n".join(lines)

    logger.debug("resolve %s/definition: no results", symbol_name)
    return f"No definition found for '{symbol_name}'."


def _resolve_references(store: SqliteStore, symbol_name: str, repo_id: int = 1) -> str:
    """Find all references to a symbol."""
    # Try SCIP first
    scip_refs = store.get_references(symbol_name, repo_id=repo_id)
    if scip_refs:
        logger.debug("resolve %s/references: %d SCIP hit(s)", symbol_name, len(scip_refs))
        lines = [f"References to '{symbol_name}' (SCIP, {len(scip_refs)} result(s)):"]
        for r in scip_refs:
            lines.append(f"  {r['file_path']}:{r['start_line']}")
        return "\n".join(lines)

    # Fall back to tree-sitter symbols (less precise — just shows where the name is declared)
    ts_syms = store.get_symbols_by_name(symbol_name, repo_id=repo_id)
    if ts_syms:
        lines = [f"Symbols named '{symbol_name}' (tree-sitter, no cross-ref data):"]
        for s in ts_syms:
            lines.append(f"  {s['file_path']}:{s['start_line']} ({s['kind']})")
        return "\n".join(lines)

    return f"No references found for '{symbol_name}'."


def _resolve_callers(store: SqliteStore, symbol_name: str, repo_id: int = 1) -> str:
    """Find symbols that call/reference the target within their definition range.

    Strategy: find the SCIP symbol matching the name, get all reference occurrences,
    then for each reference location, find which tree-sitter symbol contains that location.
    """
    scip_refs = store.get_references(symbol_name, repo_id=repo_id)
    if not scip_refs:
        logger.debug("resolve %s/callers: no SCIP references", symbol_name)
        return f"No callers found for '{symbol_name}' (no SCIP reference data)."

    callers: list[str] = []
    seen: set[str] = set()

    for ref in scip_refs:
        file_path = ref["file_path"]
        ref_line = ref["start_line"]
        # Find the tree-sitter symbol that contains this reference
        file_syms = store.get_symbols_by_file(file_path, repo_id=repo_id)
        for sym in file_syms:
            if sym["start_line"] <= ref_line <= sym["end_line"]:
                key = f"{sym['name']}@{file_path}:{sym['start_line']}"
                if key not in seen:
                    seen.add(key)
                    callers.append(
                        f"  {sym['name']} ({sym['kind']}) at {file_path}:{sym['start_line']}"
                    )

    if callers:
        logger.debug("resolve %s/callers: %d caller(s) from %d ref(s)", symbol_name, len(callers), len(scip_refs))
        header = f"Callers of '{symbol_name}' ({len(callers)} result(s)):"
        return "\n".join([header] + callers)

    logger.debug("resolve %s/callers: %d refs but no enclosing symbols", symbol_name, len(scip_refs))
    return f"No callers found for '{symbol_name}' (references exist but no enclosing symbols found)."


def _resolve_callees(store: SqliteStore, symbol_name: str, repo_id: int = 1) -> str:
    """Find symbols called/referenced within the target's definition range.

    Strategy: find the target's definition range, then find all SCIP references
    within that range that point to other symbols.
    """
    # Find the definition location
    scip_defs = store.get_definition(symbol_name, repo_id=repo_id)
    ts_syms = store.get_symbols_by_name(symbol_name, repo_id=repo_id)

    # Determine the definition range from tree-sitter (more reliable for ranges)
    def_ranges: list[tuple[str, int, int]] = []
    if ts_syms:
        for s in ts_syms:
            def_ranges.append((s["file_path"], s["start_line"], s["end_line"]))
    elif scip_defs:
        # SCIP definitions are point locations, not ranges. Use a heuristic.
        for d in scip_defs:
            def_ranges.append((d["file_path"], d["start_line"], d["start_line"] + 50))

    if not def_ranges:
        return f"No definition found for '{symbol_name}', cannot determine callees."

    # For each definition range, find SCIP references within that range
    callees: list[str] = []
    seen: set[str] = set()

    for file_path, start, end in def_ranges:
        # Get all SCIP occurrences in this file that are references
        # We query by file and filter by range
        cur = store._conn.execute(
            """SELECT ss.symbol, so.start_line
               FROM scip_occurrences so
               JOIN scip_symbols ss ON so.symbol_id = ss.id
               WHERE so.file_path = ? AND so.role = 'reference'
                 AND so.start_line >= ? AND so.start_line <= ?
                 AND so.repo_id = ?""",
            (file_path, start, end, repo_id),
        )
        for row in cur.fetchall():
            scip_symbol = row[0]
            line = row[1]
            # Extract a readable name from the SCIP symbol string
            name = _extract_name_from_scip_symbol(scip_symbol)
            key = f"{name}@{line}"
            if key not in seen and name != symbol_name:
                seen.add(key)
                callees.append(f"  {name} at {file_path}:{line}")

    if callees:
        logger.debug("resolve %s/callees: %d callee(s)", symbol_name, len(callees))
        header = f"Callees of '{symbol_name}' ({len(callees)} result(s)):"
        return "\n".join([header] + callees)

    logger.debug("resolve %s/callees: none found", symbol_name)
    return f"No callees found for '{symbol_name}'."


def _extract_name_from_scip_symbol(scip_symbol: str) -> str:
    """Extract a human-readable name from a SCIP symbol string.

    SCIP symbols look like: 'npm . vite 5.0.0 src/`module`/`functionName`().'
    We extract the last meaningful identifier.
    """
    # Remove trailing punctuation
    s = scip_symbol.rstrip("().")
    # Find backtick-quoted segments
    parts = []
    i = 0
    while i < len(s):
        if s[i] == "`":
            end = s.find("`", i + 1)
            if end != -1:
                parts.append(s[i + 1 : end])
                i = end + 1
                continue
        i += 1

    if parts:
        return parts[-1]

    # Fallback: use last space-separated segment
    segments = scip_symbol.split()
    return segments[-1] if segments else scip_symbol
