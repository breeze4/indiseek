"""Tree-sitter TypeScript/TSX parser — extract symbols and AST-scoped chunks."""

from __future__ import annotations

from pathlib import Path

import tree_sitter_typescript as ts_typescript
from tree_sitter import Language, Parser, Query, QueryCursor

from indiseek.storage.sqlite_store import Chunk, Symbol

TS_LANGUAGE = Language(ts_typescript.language_typescript())
TSX_LANGUAGE = Language(ts_typescript.language_tsx())

# ── Tree-sitter queries for symbol extraction ──
# These capture function declarations, class declarations, method definitions,
# interface declarations, type aliases, enum declarations, and exported
# variable declarations.

_SYMBOL_QUERY_SRC = """
(function_declaration
  name: (identifier) @name) @definition

(class_declaration
  name: (type_identifier) @name) @definition

(method_definition
  name: (property_identifier) @name) @definition

(interface_declaration
  name: (type_identifier) @name) @definition

(type_alias_declaration
  name: (type_identifier) @name) @definition

(enum_declaration
  name: (identifier) @name) @definition

(export_statement
  declaration: (lexical_declaration
    (variable_declarator
      name: (identifier) @name))) @definition
"""

# Map tree-sitter node types to our symbol kind strings
_NODE_KIND_MAP = {
    "function_declaration": "function",
    "class_declaration": "class",
    "method_definition": "method",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "export_statement": "variable",
}


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for code."""
    return len(text) // 4


def _first_line(node_text: bytes) -> str:
    """Extract first line of a node as its signature."""
    text = node_text.decode("utf-8", errors="replace")
    first = text.split("\n", 1)[0]
    return first[:200]


class TypeScriptParser:
    def __init__(self) -> None:
        self._ts_parser = Parser(TS_LANGUAGE)
        self._tsx_parser = Parser(TSX_LANGUAGE)
        self._ts_query = Query(TS_LANGUAGE, _SYMBOL_QUERY_SRC)
        self._tsx_query = Query(TSX_LANGUAGE, _SYMBOL_QUERY_SRC)

    def _get_parser_and_query(self, path: Path) -> tuple[Parser, Query, Language]:
        if path.suffix == ".tsx":
            return self._tsx_parser, self._tsx_query, TSX_LANGUAGE
        return self._ts_parser, self._ts_query, TS_LANGUAGE

    def parse_file(self, path: Path, relative_path: str) -> list[Symbol]:
        """Extract symbols from a TypeScript/TSX file."""
        source = path.read_bytes()
        parser, query, _lang = self._get_parser_and_query(path)
        tree = parser.parse(source)

        cursor = QueryCursor(query)
        matches = cursor.matches(tree.root_node)

        symbols: list[Symbol] = []
        for _pattern_idx, captures in matches:
            def_nodes = captures.get("definition", [])
            name_nodes = captures.get("name", [])
            if not def_nodes or not name_nodes:
                continue

            def_node = def_nodes[0]
            name_node = name_nodes[0]

            # Determine kind from the definition node type
            node_type = def_node.type
            # For export_statement wrapping a lexical_declaration, check inner
            kind = _NODE_KIND_MAP.get(node_type, "variable")

            symbols.append(
                Symbol(
                    id=None,
                    file_path=relative_path,
                    name=name_node.text.decode("utf-8"),
                    kind=kind,
                    start_line=def_node.start_point[0] + 1,  # 1-indexed
                    start_col=def_node.start_point[1],
                    end_line=def_node.end_point[0] + 1,
                    end_col=def_node.end_point[1],
                    signature=_first_line(def_node.text),
                )
            )

        return symbols

    def chunk_file(self, path: Path, relative_path: str) -> list[Chunk]:
        """Produce AST-scoped chunks from a TypeScript/TSX file.

        One chunk per top-level symbol (function, class, etc).
        Falls back to a single file-level chunk if no symbols found.
        """
        source = path.read_bytes()
        source_text = source.decode("utf-8", errors="replace")
        parser, query, _lang = self._get_parser_and_query(path)
        tree = parser.parse(source)

        cursor = QueryCursor(query)
        matches = cursor.matches(tree.root_node)

        chunks: list[Chunk] = []
        covered_lines: set[int] = set()

        for _pattern_idx, captures in matches:
            def_nodes = captures.get("definition", [])
            name_nodes = captures.get("name", [])
            if not def_nodes or not name_nodes:
                continue

            def_node = def_nodes[0]
            name_node = name_nodes[0]

            start_line = def_node.start_point[0] + 1
            end_line = def_node.end_point[0] + 1
            content = def_node.text.decode("utf-8", errors="replace")
            node_type = def_node.type
            kind = _NODE_KIND_MAP.get(node_type, "variable")

            chunks.append(
                Chunk(
                    id=None,
                    file_path=relative_path,
                    symbol_name=name_node.text.decode("utf-8"),
                    chunk_type=kind,
                    start_line=start_line,
                    end_line=end_line,
                    content=content,
                    token_estimate=_estimate_tokens(content),
                )
            )
            covered_lines.update(range(start_line, end_line + 1))

        # If we got no chunks, or there's significant uncovered code at the
        # module level, add a module_header chunk for the whole file.
        lines = source_text.split("\n")
        total_lines = len(lines)
        if not chunks:
            chunks.append(
                Chunk(
                    id=None,
                    file_path=relative_path,
                    symbol_name=None,
                    chunk_type="module",
                    start_line=1,
                    end_line=total_lines,
                    content=source_text,
                    token_estimate=_estimate_tokens(source_text),
                )
            )

        return chunks
