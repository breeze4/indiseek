"""Load SCIP protobuf index into SQLite."""

from __future__ import annotations

from pathlib import Path

from indiseek.indexer import scip_pb2
from indiseek.storage.sqlite_store import SqliteStore

# Bit flag for definition role in SCIP SymbolRole enum
_DEFINITION_ROLE = 0x1


def _parse_range(r: list[int]) -> tuple[int, int, int, int]:
    """Convert SCIP range (3 or 4 ints) to (start_line, start_col, end_line, end_col).

    SCIP ranges are 0-based. We store them as-is (0-based) to match SCIP conventions.
    """
    if len(r) == 4:
        return (r[0], r[1], r[2], r[3])
    elif len(r) == 3:
        # Three elements: [startLine, startChar, endChar] — end line == start line
        return (r[0], r[1], r[0], r[2])
    else:
        raise ValueError(f"Invalid SCIP range: {r}")


class ScipLoader:
    """Loads a SCIP protobuf index file into SQLite storage."""

    def __init__(self, store: SqliteStore) -> None:
        self._store = store

    def load(self, scip_path: Path) -> dict[str, int]:
        """Load a SCIP index file into the database.

        Returns a dict with counts: symbols, occurrences, relationships.
        """
        data = scip_path.read_bytes()
        index = scip_pb2.Index()
        index.ParseFromString(data)

        counts = {"symbols": 0, "occurrences": 0, "relationships": 0}

        for doc in index.documents:
            file_path = doc.relative_path

            # Process symbol information (definitions within this document)
            for sym_info in doc.symbols:
                if not sym_info.symbol or sym_info.symbol.startswith("local "):
                    continue

                doc_text = "\n".join(sym_info.documentation) if sym_info.documentation else None
                sym_id = self._store.insert_scip_symbol(sym_info.symbol, doc_text)
                counts["symbols"] += 1

                # Process relationships
                for rel in sym_info.relationships:
                    if not rel.symbol or rel.symbol.startswith("local "):
                        continue

                    related_id = self._store.insert_scip_symbol(rel.symbol)

                    rel_types = []
                    if rel.is_implementation:
                        rel_types.append("implementation")
                    if rel.is_type_definition:
                        rel_types.append("type_definition")
                    if rel.is_reference:
                        rel_types.append("reference")
                    if rel.is_definition:
                        rel_types.append("definition")

                    for rel_type in rel_types:
                        self._store.insert_scip_relationship(sym_id, related_id, rel_type)
                        counts["relationships"] += 1

            # Process occurrences
            occ_batch: list[tuple[int, str, int, int, int, int, str]] = []
            for occ in doc.occurrences:
                if not occ.symbol or occ.symbol.startswith("local "):
                    continue

                sym_id = self._store.insert_scip_symbol(occ.symbol)

                try:
                    start_line, start_col, end_line, end_col = _parse_range(list(occ.range))
                except ValueError:
                    continue

                role = "definition" if (occ.symbol_roles & _DEFINITION_ROLE) else "reference"
                occ_batch.append((sym_id, file_path, start_line, start_col, end_line, end_col, role))

            if occ_batch:
                self._store.insert_scip_occurrences(occ_batch)
                counts["occurrences"] += len(occ_batch)

        return counts
