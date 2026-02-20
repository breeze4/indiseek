"""Tests for SCIP loader and SCIP-related SQLite operations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from indiseek.indexer.scip import ScipLoader, _parse_range
from indiseek.indexer import scip_pb2

if TYPE_CHECKING:
    from indiseek.storage.sqlite_store import SqliteStore


def _make_scip_index(
    documents: list[scip_pb2.Document] | None = None,
) -> bytes:
    """Build a minimal SCIP index protobuf and return serialized bytes."""
    index = scip_pb2.Index()
    index.metadata.CopyFrom(scip_pb2.Metadata())
    if documents:
        for doc in documents:
            index.documents.append(doc)
    return index.SerializeToString()


def _make_document(
    path: str,
    occurrences: list[scip_pb2.Occurrence] | None = None,
    symbols: list[scip_pb2.SymbolInformation] | None = None,
) -> scip_pb2.Document:
    doc = scip_pb2.Document()
    doc.relative_path = path
    if occurrences:
        for occ in occurrences:
            doc.occurrences.append(occ)
    if symbols:
        for sym in symbols:
            doc.symbols.append(sym)
    return doc


def _make_occurrence(
    symbol: str,
    range_vals: list[int],
    symbol_roles: int = 0,
) -> scip_pb2.Occurrence:
    occ = scip_pb2.Occurrence()
    occ.symbol = symbol
    occ.range.extend(range_vals)
    occ.symbol_roles = symbol_roles
    return occ


def _make_symbol_info(
    symbol: str,
    documentation: list[str] | None = None,
    relationships: list[scip_pb2.Relationship] | None = None,
) -> scip_pb2.SymbolInformation:
    si = scip_pb2.SymbolInformation()
    si.symbol = symbol
    if documentation:
        si.documentation.extend(documentation)
    if relationships:
        for rel in relationships:
            si.relationships.append(rel)
    return si


def _make_relationship(
    symbol: str,
    is_implementation: bool = False,
    is_reference: bool = False,
    is_type_definition: bool = False,
) -> scip_pb2.Relationship:
    rel = scip_pb2.Relationship()
    rel.symbol = symbol
    rel.is_implementation = is_implementation
    rel.is_reference = is_reference
    rel.is_type_definition = is_type_definition
    return rel


class TestParseRange:
    def test_four_element_range(self) -> None:
        assert _parse_range([10, 5, 15, 20]) == (10, 5, 15, 20)

    def test_three_element_range(self) -> None:
        # Three elements: [startLine, startChar, endChar]
        assert _parse_range([10, 5, 20]) == (10, 5, 10, 20)

    def test_invalid_range(self) -> None:
        with pytest.raises(ValueError, match="Invalid SCIP range"):
            _parse_range([1, 2])


class TestScipStoreMethods:
    def test_insert_and_lookup_scip_symbol(self, store: SqliteStore) -> None:
        sym_id = store.insert_scip_symbol("npm . vite 5.0.0 src/`createServer`().")
        assert sym_id is not None
        assert sym_id > 0

        # Duplicate insert returns same id
        sym_id2 = store.insert_scip_symbol("npm . vite 5.0.0 src/`createServer`().")
        assert sym_id2 == sym_id

    def test_insert_scip_symbol_with_docs(self, store: SqliteStore) -> None:
        sym_id = store.insert_scip_symbol(
            "npm . vite 5.0.0 src/`createServer`().",
            "Creates the Vite dev server",
        )
        assert sym_id > 0

    def test_insert_scip_occurrences(self, store: SqliteStore) -> None:
        sym_id = store.insert_scip_symbol("test_sym")
        store.insert_scip_occurrences([
            (sym_id, "src/index.ts", 10, 0, 10, 12, "definition"),
            (sym_id, "src/other.ts", 5, 2, 5, 14, "reference"),
        ])
        assert store.count("scip_occurrences") == 2

    def test_insert_scip_relationship(self, store: SqliteStore) -> None:
        sym_a = store.insert_scip_symbol("sym_a")
        sym_b = store.insert_scip_symbol("sym_b")
        store.insert_scip_relationship(sym_a, sym_b, "implementation")
        assert store.count("scip_relationships") == 1

    def test_get_definition(self, store: SqliteStore) -> None:
        sym_id = store.insert_scip_symbol("npm . pkg 1.0 `createServer`().")
        store.insert_scip_occurrences([
            (sym_id, "src/server.ts", 42, 0, 42, 12, "definition"),
            (sym_id, "src/main.ts", 10, 5, 10, 17, "reference"),
        ])

        defs = store.get_definition("createServer")
        assert len(defs) == 1
        assert defs[0]["file_path"] == "src/server.ts"
        assert defs[0]["start_line"] == 42

    def test_get_references(self, store: SqliteStore) -> None:
        sym_id = store.insert_scip_symbol("npm . pkg 1.0 `createServer`().")
        store.insert_scip_occurrences([
            (sym_id, "src/server.ts", 42, 0, 42, 12, "definition"),
            (sym_id, "src/main.ts", 10, 5, 10, 17, "reference"),
            (sym_id, "src/test.ts", 3, 0, 3, 12, "reference"),
        ])

        refs = store.get_references("createServer")
        assert len(refs) == 2
        paths = {r["file_path"] for r in refs}
        assert paths == {"src/main.ts", "src/test.ts"}

    def test_get_scip_occurrences_by_symbol_id(self, store: SqliteStore) -> None:
        sym_id = store.insert_scip_symbol("test_sym")
        store.insert_scip_occurrences([
            (sym_id, "a.ts", 1, 0, 1, 5, "definition"),
            (sym_id, "b.ts", 3, 2, 3, 7, "reference"),
        ])
        occs = store.get_scip_occurrences_by_symbol_id(sym_id)
        assert len(occs) == 2

    def test_get_scip_relationships_for(self, store: SqliteStore) -> None:
        sym_a = store.insert_scip_symbol("sym_a")
        sym_b = store.insert_scip_symbol("sym_b")
        store.insert_scip_relationship(sym_a, sym_b, "implementation")

        rels = store.get_scip_relationships_for(sym_a)
        assert len(rels) == 1
        assert rels[0]["relationship"] == "implementation"
        assert rels[0]["related_symbol"] == "sym_b"


class TestScipLoader:
    def test_load_empty_index(self, store: SqliteStore, tmp_path: Path) -> None:
        data = _make_scip_index()
        scip_file = tmp_path / "index.scip"
        scip_file.write_bytes(data)

        loader = ScipLoader(store)
        counts = loader.load(scip_file)
        assert counts["symbols"] == 0
        assert counts["occurrences"] == 0
        assert counts["relationships"] == 0

    def test_load_definitions_and_references(
        self, store: SqliteStore, tmp_path: Path
    ) -> None:
        sym_str = "npm . vite 5.0.0 `createServer`()."
        doc = _make_document(
            "src/server.ts",
            occurrences=[
                _make_occurrence(sym_str, [42, 0, 42, 12], symbol_roles=0x1),  # definition
                _make_occurrence(sym_str, [100, 5, 100, 17], symbol_roles=0),  # reference
            ],
            symbols=[
                _make_symbol_info(sym_str, documentation=["Creates the dev server"]),
            ],
        )
        data = _make_scip_index(documents=[doc])
        scip_file = tmp_path / "index.scip"
        scip_file.write_bytes(data)

        loader = ScipLoader(store)
        counts = loader.load(scip_file)

        assert counts["symbols"] >= 1
        assert counts["occurrences"] == 2

        defs = store.get_definition("createServer")
        assert len(defs) == 1
        assert defs[0]["start_line"] == 42

        refs = store.get_references("createServer")
        assert len(refs) == 1
        assert refs[0]["start_line"] == 100

    def test_load_relationships(
        self, store: SqliteStore, tmp_path: Path
    ) -> None:
        animal_sym = "npm . pkg 1.0 `Animal`#"
        dog_sym = "npm . pkg 1.0 `Dog`#"
        doc = _make_document(
            "src/animals.ts",
            symbols=[
                _make_symbol_info(animal_sym),
                _make_symbol_info(
                    dog_sym,
                    relationships=[
                        _make_relationship(animal_sym, is_implementation=True),
                    ],
                ),
            ],
            occurrences=[
                _make_occurrence(animal_sym, [1, 0, 1, 6], symbol_roles=0x1),
                _make_occurrence(dog_sym, [5, 0, 5, 3], symbol_roles=0x1),
            ],
        )
        data = _make_scip_index(documents=[doc])
        scip_file = tmp_path / "index.scip"
        scip_file.write_bytes(data)

        loader = ScipLoader(store)
        counts = loader.load(scip_file)

        assert counts["relationships"] == 1

        dog_id = store.get_scip_symbol_id(dog_sym)
        assert dog_id is not None
        rels = store.get_scip_relationships_for(dog_id)
        assert len(rels) == 1
        assert rels[0]["relationship"] == "implementation"
        assert rels[0]["related_symbol"] == animal_sym

    def test_load_multi_document(
        self, store: SqliteStore, tmp_path: Path
    ) -> None:
        sym = "npm . vite 5.0.0 `transform`()."
        doc1 = _make_document(
            "src/a.ts",
            occurrences=[_make_occurrence(sym, [10, 0, 20], symbol_roles=0x1)],
            symbols=[_make_symbol_info(sym)],
        )
        doc2 = _make_document(
            "src/b.ts",
            occurrences=[_make_occurrence(sym, [5, 2, 14], symbol_roles=0)],
        )
        data = _make_scip_index(documents=[doc1, doc2])
        scip_file = tmp_path / "index.scip"
        scip_file.write_bytes(data)

        loader = ScipLoader(store)
        counts = loader.load(scip_file)

        assert counts["occurrences"] == 2
        defs = store.get_definition("transform")
        assert len(defs) == 1
        assert defs[0]["file_path"] == "src/a.ts"

        refs = store.get_references("transform")
        assert len(refs) == 1
        assert refs[0]["file_path"] == "src/b.ts"

    def test_local_symbols_skipped(
        self, store: SqliteStore, tmp_path: Path
    ) -> None:
        doc = _make_document(
            "src/a.ts",
            occurrences=[
                _make_occurrence("local 1", [1, 0, 5], symbol_roles=0x1),
                _make_occurrence("npm . vite 5.0 `foo`.", [2, 0, 3], symbol_roles=0),
            ],
            symbols=[
                _make_symbol_info("local 1"),
                _make_symbol_info("npm . vite 5.0 `foo`."),
            ],
        )
        data = _make_scip_index(documents=[doc])
        scip_file = tmp_path / "index.scip"
        scip_file.write_bytes(data)

        loader = ScipLoader(store)
        counts = loader.load(scip_file)

        # Only the non-local symbol should be loaded
        assert counts["occurrences"] == 1
        assert store.count("scip_symbols") == 1

    def test_three_element_range_handling(
        self, store: SqliteStore, tmp_path: Path
    ) -> None:
        sym = "npm . pkg 1.0 `fn`()."
        doc = _make_document(
            "src/x.ts",
            occurrences=[
                # 3-element range: [startLine, startChar, endChar]
                _make_occurrence(sym, [10, 5, 20], symbol_roles=0x1),
            ],
            symbols=[_make_symbol_info(sym)],
        )
        data = _make_scip_index(documents=[doc])
        scip_file = tmp_path / "index.scip"
        scip_file.write_bytes(data)

        loader = ScipLoader(store)
        loader.load(scip_file)

        defs = store.get_definition("fn")
        assert len(defs) == 1
        assert defs[0]["start_line"] == 10
        assert defs[0]["start_col"] == 5
        assert defs[0]["end_line"] == 10  # same as start_line for 3-element range
        assert defs[0]["end_col"] == 20
