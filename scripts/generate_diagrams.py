#!/usr/bin/env python3
"""Generate Excalidraw diagrams for the Indiseek indexing pipeline."""

import json
from pathlib import Path

DIAGRAMS_DIR = Path(__file__).resolve().parent.parent / "docs" / "diagrams"

# Colors
GREEN = "#b2f2bb"       # local/free steps
ORANGE = "#ffec99"      # API-cost steps
BLUE = "#a5d8ff"        # storage outputs
GRAY = "#dee2e6"        # inputs
WHITE = "#ffffff"
DARK = "#1e1e1e"
MUTED = "#868e96"
DARK_GREEN = "#2b8a3e"
DARK_ORANGE = "#e67700"
DARK_BLUE = "#1971c2"

_id_counter = 0


def _next_id(prefix: str) -> str:
    global _id_counter
    _id_counter += 1
    return f"{prefix}-{_id_counter}"


def _reset_ids():
    global _id_counter
    _id_counter = 0


def _rect(
    id: str, x: float, y: float, w: float, h: float,
    bg: str, stroke: str = DARK, bound_arrows: list | None = None,
):
    be = []
    if bound_arrows:
        be.extend({"id": aid, "type": "arrow"} for aid in bound_arrows)
    return {
        "id": id, "type": "rectangle",
        "x": x, "y": y, "width": w, "height": h,
        "strokeColor": stroke, "backgroundColor": bg,
        "fillStyle": "solid", "strokeWidth": 2, "roughness": 1,
        "opacity": 100, "angle": 0, "groupIds": [],
        "roundness": {"type": 3},
        "boundElements": be, "isDeleted": False,
    }


def _text(
    id: str, x: float, y: float, content: str,
    size: int = 16, color: str = DARK, family: int = 1,
    align: str = "center", valign: str = "top",
    container_id: str | None = None,
):
    lines = content.split("\n")
    line_height = size * 1.35
    h = line_height * len(lines)
    max_line = max(len(l) for l in lines)
    w = max_line * size * 0.55

    el = {
        "id": id, "type": "text",
        "x": x, "y": y, "width": w, "height": h,
        "text": content, "fontSize": size, "fontFamily": family,
        "textAlign": align, "verticalAlign": valign,
        "strokeColor": color, "isDeleted": False,
        "opacity": 100, "angle": 0, "groupIds": [],
        "rawText": content,
    }
    if container_id:
        el["containerId"] = container_id
    return el


def _arrow(
    id: str, x: float, y: float, dx: float, dy: float,
    start_id: str | None = None, end_id: str | None = None,
    stroke: str = DARK, width: int = 2,
    start_head: str | None = None, end_head: str = "arrow",
):
    el = {
        "id": id, "type": "arrow",
        "x": x, "y": y, "width": abs(dx), "height": abs(dy),
        "points": [[0, 0], [dx, dy]],
        "startArrowhead": start_head, "endArrowhead": end_head,
        "strokeColor": stroke, "strokeWidth": width, "roughness": 1,
        "opacity": 100, "angle": 0, "groupIds": [],
        "isDeleted": False, "boundElements": [],
    }
    if start_id:
        el["startBinding"] = {"elementId": start_id, "focus": 0, "gap": 5}
    if end_id:
        el["endBinding"] = {"elementId": end_id, "focus": 0, "gap": 5}
    return el


def _make_file(elements: list) -> dict:
    return {
        "type": "excalidraw",
        "version": 2,
        "source": "https://excalidraw.com",
        "elements": elements,
        "appState": {"viewBackgroundColor": WHITE, "gridSize": 20},
        "files": {},
    }


def _labeled_box(
    id: str, x: float, y: float, w: float, h: float,
    bg: str, label: str, sublabel: str | None = None,
    stroke: str = DARK, font_size: int = 16, sub_color: str = MUTED,
    bound_arrows: list | None = None,
) -> list:
    """Create a rectangle with a centered text label and optional sublabel."""
    elements = []
    text_id = f"{id}-text"
    ba = bound_arrows or []

    # Rectangle
    r = _rect(id, x, y, w, h, bg, stroke, ba)
    r["boundElements"].append({"id": text_id, "type": "text"})
    elements.append(r)

    # Main label text (bound to container)
    full_text = label
    if sublabel:
        full_text = f"{label}\n{sublabel}"
    elements.append(_text(
        text_id, x + 10, y + 10, full_text,
        size=font_size, color=DARK, align="center", valign="middle",
        container_id=id,
    ))

    return elements


# ─── Overview Diagram ─────────────────────────────────────────────────────

def build_overview() -> dict:
    _reset_ids()
    elements = []

    # Layout constants
    sx = 100       # step boxes x
    sw = 300       # step box width
    sh = 80        # step box height
    gap = 30       # vertical gap
    ox = 500       # output boxes x
    ow = 220       # output box width
    oh = 60        # output box height

    # Title
    elements.append(_text("title", sx, 10, "Indiseek Indexing Pipeline",
                          size=28, color=DARK, family=2, align="left"))
    elements.append(_text("subtitle", sx, 48,
                          "scripts/index.py — 5-step codebase indexing",
                          size=14, color=MUTED, family=2, align="left"))

    # Starting Y for first box
    y = 90

    # ── Input box ──
    input_id = "input"
    input_arrow = "a-input-s1"
    elements.extend(_labeled_box(
        input_id, sx, y, sw, 60, GRAY,
        "Git Repository", ".ts / .tsx files",
        bound_arrows=[input_arrow],
    ))

    y += 60 + gap

    # ── Step definitions ──
    steps = [
        {
            "id": "s1", "label": "1. Tree-sitter Parse",
            "sub": "LOCAL \u00b7 FREE", "bg": GREEN,
            "out_id": "o1", "out_label": "SQLite",
            "out_sub": "symbols + chunks",
        },
        {
            "id": "s2", "label": "2. SCIP Cross-References",
            "sub": "LOCAL \u00b7 FREE", "bg": GREEN,
            "out_id": "o2", "out_label": "SQLite",
            "out_sub": "xrefs + occurrences",
        },
        {
            "id": "s3", "label": "3. Semantic Embedding",
            "sub": "GEMINI API \u00b7 ~$0.06", "bg": ORANGE,
            "out_id": "o3", "out_label": "LanceDB",
            "out_sub": "768-dim vectors",
        },
        {
            "id": "s4", "label": "4. File Summarization",
            "sub": "GEMINI API \u00b7 ~$0.14", "bg": ORANGE,
            "out_id": "o4", "out_label": "SQLite",
            "out_sub": "file_summaries",
        },
        {
            "id": "s5", "label": "5. Lexical Index",
            "sub": "LOCAL \u00b7 FREE", "bg": GREEN,
            "out_id": "o5", "out_label": "Tantivy",
            "out_sub": "BM25 index",
        },
    ]

    prev_id = input_id
    for i, step in enumerate(steps):
        sid = step["id"]
        oid = step["out_id"]
        v_arrow = f"a-{prev_id}-{sid}"
        h_arrow = f"a-{sid}-{oid}"

        # Step box
        elements.extend(_labeled_box(
            sid, sx, y, sw, sh, step["bg"],
            step["label"], step["sub"],
            bound_arrows=[v_arrow, h_arrow],
        ))

        # Output box (aligned vertically with step, to the right)
        out_y = y + (sh - oh) / 2
        elements.extend(_labeled_box(
            oid, ox, out_y, ow, oh, BLUE,
            step["out_label"], step["out_sub"],
            stroke=DARK_BLUE,
            bound_arrows=[h_arrow],
        ))

        # Vertical arrow from previous to this step
        elements.append(_arrow(
            v_arrow, sx + sw / 2, y - gap, 0, gap,
            start_id=prev_id, end_id=sid,
        ))

        # Horizontal arrow from step to output
        elements.append(_arrow(
            h_arrow, sx + sw, y + sh / 2, ox - sx - sw, 0,
            start_id=sid, end_id=oid,
        ))

        prev_id = sid
        y += sh + gap

    # ── Flag annotations ──
    # Small text labels showing which flags enable which steps
    flag_x = sx - 10
    flag_positions = {
        "s1": ("always", DARK_GREEN),
        "s2": ("if index.scip exists", DARK_GREEN),
        "s3": ("--embed", DARK_ORANGE),
        "s4": ("--summarize", DARK_ORANGE),
        "s5": ("--lexical", DARK_GREEN),
    }
    for i, step in enumerate(steps):
        label, color = flag_positions[step["id"]]
        box_y = 90 + 60 + gap + i * (sh + gap)
        elements.append(_text(
            f"flag-{step['id']}", flag_x - 100, box_y + sh / 2 - 8,
            label, size=13, color=color, family=3, align="right",
        ))

    return _make_file(elements)


# ─── Detail: Step 1 — Tree-sitter ────────────────────────────────────────

def build_step1_detail() -> dict:
    _reset_ids()
    elements = []

    elements.append(_text("title", 80, 20,
                          "Step 1: Tree-sitter Parsing", size=24, family=2))
    elements.append(_text("cost", 80, 52,
                          "LOCAL \u00b7 FREE \u00b7 Parses thousands of files in seconds",
                          size=13, color=MUTED, family=2))

    y = 90

    # Input
    elements.extend(_labeled_box("in1", 80, y, 200, 50, GRAY,
                                 "git ls-files", "tracked .ts/.tsx",
                                 bound_arrows=["a1"]))

    # Tree-sitter parser
    elements.extend(_labeled_box("ts", 380, y, 220, 50, GREEN,
                                 "Tree-sitter Parser", "AST generation",
                                 bound_arrows=["a1", "a2", "a3"]))

    elements.append(_arrow("a1", 280, y + 25, 100, 0,
                           start_id="in1", end_id="ts"))

    y2 = y + 90

    # Symbol extraction
    elements.extend(_labeled_box("sym", 280, y2, 180, 60, "#d0bfff",
                                 "Symbol Extraction",
                                 "functions, classes,\ninterfaces, types",
                                 bound_arrows=["a2", "a4"]))

    # Chunk extraction
    elements.extend(_labeled_box("chk", 520, y2, 180, 60, "#d0bfff",
                                 "AST Chunking",
                                 "scope-based splits\n~4 chars/token",
                                 bound_arrows=["a3", "a5"]))

    elements.append(_arrow("a2", 440, y + 50, -50, y2 - y - 50,
                           start_id="ts", end_id="sym"))
    elements.append(_arrow("a3", 540, y + 50, 50, y2 - y - 50,
                           start_id="ts", end_id="chk"))

    y3 = y2 + 100

    # SQLite symbols
    elements.extend(_labeled_box("db1", 280, y3, 180, 50, BLUE,
                                 "symbols table",
                                 "name, kind, file, lines",
                                 stroke=DARK_BLUE,
                                 bound_arrows=["a4"]))

    # SQLite chunks
    elements.extend(_labeled_box("db2", 520, y3, 180, 50, BLUE,
                                 "chunks table",
                                 "content, file, token_est",
                                 stroke=DARK_BLUE,
                                 bound_arrows=["a5"]))

    elements.append(_arrow("a4", 370, y2 + 60, 0, y3 - y2 - 60,
                           start_id="sym", end_id="db1"))
    elements.append(_arrow("a5", 610, y2 + 60, 0, y3 - y2 - 60,
                           start_id="chk", end_id="db2"))

    # SQLite label
    elements.append(_text("sqlite-label", 380, y3 + 55,
                          "SQLite (indiseek.db)",
                          size=13, color=MUTED, family=3))

    # Detail annotations
    y4 = y3 + 90
    details = [
        "Symbols: function, class, method, interface, type, enum, variable",
        "Chunks: one per top-level symbol; fallback module chunk for bare files",
        "Query: tree-sitter pattern matching on AST node types",
    ]
    for i, d in enumerate(details):
        elements.append(_text(f"detail-{i}", 80, y4 + i * 22,
                              d, size=12, color=MUTED, family=3, align="left"))

    return _make_file(elements)


# ─── Detail: Step 2 — SCIP ───────────────────────────────────────────────

def build_step2_detail() -> dict:
    _reset_ids()
    elements = []

    elements.append(_text("title", 80, 20,
                          "Step 2: SCIP Cross-References", size=24, family=2))
    elements.append(_text("cost", 80, 52,
                          "LOCAL \u00b7 FREE \u00b7 Pre-generated by scip-typescript (Node.js)",
                          size=13, color=MUTED, family=2))

    y = 90

    # Inputs
    elements.extend(_labeled_box("scip-file", 80, y, 200, 50, GRAY,
                                 "index.scip", "protobuf binary",
                                 bound_arrows=["a1"]))

    elements.extend(_labeled_box("loader", 380, y, 220, 50, GREEN,
                                 "ScipLoader", "protobuf deserialize",
                                 bound_arrows=["a1", "a2", "a3", "a4"]))

    elements.append(_arrow("a1", 280, y + 25, 100, 0,
                           start_id="scip-file", end_id="loader"))

    y2 = y + 90

    # Three output tables
    tw = 170
    elements.extend(_labeled_box("t1", 180, y2, tw, 60, BLUE,
                                 "scip_symbols",
                                 "symbol ID + docs",
                                 stroke=DARK_BLUE,
                                 bound_arrows=["a2"]))

    elements.extend(_labeled_box("t2", 380, y2, tw, 60, BLUE,
                                 "scip_occurrences",
                                 "file, line, col, role",
                                 stroke=DARK_BLUE,
                                 bound_arrows=["a3"]))

    elements.extend(_labeled_box("t3", 580, y2, tw, 60, BLUE,
                                 "scip_relationships",
                                 "impl, typedef, ref",
                                 stroke=DARK_BLUE,
                                 bound_arrows=["a4"]))

    elements.append(_arrow("a2", 430, y + 50, -80, y2 - y - 50,
                           start_id="loader", end_id="t1"))
    elements.append(_arrow("a3", 490, y + 50, 0, y2 - y - 50,
                           start_id="loader", end_id="t2"))
    elements.append(_arrow("a4", 550, y + 50, 80, y2 - y - 50,
                           start_id="loader", end_id="t3"))

    # Capabilities box
    y3 = y2 + 100
    elements.extend(_labeled_box("cap", 180, y3, 570, 60, "#fff3bf",
                                 "Enables: go-to-definition, find-references, callers, callees",
                                 "Used by resolve_symbol tool at query time",
                                 stroke=DARK_ORANGE))

    # Details
    y4 = y3 + 80
    details = [
        "Skips local symbols (scoped to a single function/block)",
        "SCIP ranges: 0-based [startLine, startCol, endLine, endCol]",
        "Roles: definition vs reference (from SCIP SymbolRole bit flags)",
        "Generation: npx scip-typescript index --infer-tsconfig",
    ]
    for i, d in enumerate(details):
        elements.append(_text(f"d-{i}", 80, y4 + i * 22,
                              d, size=12, color=MUTED, family=3, align="left"))

    return _make_file(elements)


# ─── Detail: Step 3 — Embedding ──────────────────────────────────────────

def build_step3_detail() -> dict:
    _reset_ids()
    elements = []

    elements.append(_text("title", 80, 20,
                          "Step 3: Semantic Embedding", size=24, family=2))
    elements.append(_text("cost", 80, 52,
                          "GEMINI API \u00b7 ~$0.06 for Vite (377k tokens) \u00b7 $0.15/1M input tokens",
                          size=13, color=DARK_ORANGE, family=2))

    y = 100

    # Input: chunks from SQLite
    elements.extend(_labeled_box("chunks", 80, y, 180, 50, BLUE,
                                 "chunks table", "from Step 1",
                                 stroke=DARK_BLUE,
                                 bound_arrows=["a1"]))

    # Skip already-embedded
    elements.extend(_labeled_box("skip", 340, y, 180, 50, "#e9ecef",
                                 "Skip existing", "resume support",
                                 bound_arrows=["a1", "a2"]))

    elements.append(_arrow("a1", 260, y + 25, 80, 0,
                           start_id="chunks", end_id="skip"))

    # Batch
    y2 = y + 80
    elements.extend(_labeled_box("batch", 340, y2, 180, 50, GREEN,
                                 "Batch (size 20)", "group chunks",
                                 bound_arrows=["a2", "a3"]))

    elements.append(_arrow("a2", 430, y + 50, 0, y2 - y - 50,
                           start_id="skip", end_id="batch"))

    # API call
    y3 = y2 + 80
    elements.extend(_labeled_box("api", 340, y3, 180, 60, ORANGE,
                                 "Gemini Embedding API",
                                 "gemini-embedding-001\n768 dimensions",
                                 bound_arrows=["a3", "a4"]))

    elements.append(_arrow("a3", 430, y2 + 50, 0, y3 - y2 - 50,
                           start_id="batch", end_id="api"))

    # Retry logic annotation
    elements.extend(_labeled_box("retry", 580, y3, 160, 60, "#ffe3e3",
                                 "Retry Logic",
                                 "1 retry per batch\n3 consecutive = abort",
                                 stroke="#c92a2a"))

    elements.append(_arrow("a-retry", 520, y3 + 30, 60, 0,
                           start_id="api", end_id="retry"))

    # Output
    y4 = y3 + 100
    elements.extend(_labeled_box("lance", 340, y4, 180, 50, BLUE,
                                 "LanceDB", "vector store on disk",
                                 stroke=DARK_BLUE,
                                 bound_arrows=["a4"]))

    elements.append(_arrow("a4", 430, y3 + 60, 0, y4 - y3 - 60,
                           start_id="api", end_id="lance"))

    # Metadata stored
    y5 = y4 + 70
    elements.append(_text("meta", 340, y5,
                          "Stored per vector: chunk_id, file_path, symbol_name, chunk_type, content",
                          size=12, color=MUTED, family=3, align="left"))

    return _make_file(elements)


# ─── Detail: Step 4 — Summarization ──────────────────────────────────────

def build_step4_detail() -> dict:
    _reset_ids()
    elements = []

    elements.append(_text("title", 80, 20,
                          "Step 4: File Summarization", size=24, family=2))
    elements.append(_text("cost", 80, 52,
                          "GEMINI API \u00b7 ~$0.14 for 1857 files \u00b7 $0.10/1M input \u00b7 $0.40/1M output",
                          size=13, color=DARK_ORANGE, family=2))

    y = 100

    # Input: source files
    elements.extend(_labeled_box("files", 80, y, 180, 60, GRAY,
                                 "Source Files",
                                 ".ts .tsx .js .jsx\n.json .md .yaml",
                                 bound_arrows=["a1"]))

    # Filter
    elements.extend(_labeled_box("filter", 340, y, 180, 60, "#e9ecef",
                                 "Filter & Resume",
                                 "skip summarized\nskip SKIP_DIRS",
                                 bound_arrows=["a1", "a2"]))

    elements.append(_arrow("a1", 260, y + 30, 80, 0,
                           start_id="files", end_id="filter"))

    # Per-file LLM call
    y2 = y + 100
    elements.extend(_labeled_box("llm", 280, y2, 280, 80, ORANGE,
                                 "Gemini 2.0 Flash",
                                 'System: "Summarize responsibility\nin one sentence"\nTruncate at 30k chars',
                                 bound_arrows=["a2", "a3"]))

    elements.append(_arrow("a2", 430, y + 60, 0, y2 - y - 60,
                           start_id="filter", end_id="llm"))

    # Rate limit annotation
    elements.extend(_labeled_box("rate", 620, y2, 150, 50, "#e9ecef",
                                 "0.5s delay", "between files",
                                 bound_arrows=["a-rate"]))
    elements.append(_arrow("a-rate", 560, y2 + 30, 60, 0,
                           start_id="llm", end_id="rate"))

    # Output
    y3 = y2 + 120
    elements.extend(_labeled_box("db", 280, y3, 280, 50, BLUE,
                                 "SQLite: file_summaries",
                                 "path, summary, language, line_count",
                                 stroke=DARK_BLUE,
                                 bound_arrows=["a3"]))

    elements.append(_arrow("a3", 420, y2 + 80, 0, y3 - y2 - 80,
                           start_id="llm", end_id="db"))

    # Usage
    y4 = y3 + 70
    elements.extend(_labeled_box("usage", 280, y4, 280, 50, "#fff3bf",
                                 "Powers: read_map tool",
                                 "directory tree + file summaries",
                                 stroke=DARK_ORANGE))

    # Details
    y5 = y4 + 70
    details = [
        "Most API-intensive step: 1 LLM call per source file",
        "Slowest wall-clock time due to per-file rate-limiting delay",
        "Resume: skips files that already have summaries in SQLite",
        "Abort: 5 consecutive API failures triggers abort",
    ]
    for i, d in enumerate(details):
        elements.append(_text(f"d-{i}", 80, y5 + i * 22,
                              d, size=12, color=MUTED, family=3, align="left"))

    return _make_file(elements)


# ─── Detail: Step 5 — Lexical Index ──────────────────────────────────────

def build_step5_detail() -> dict:
    _reset_ids()
    elements = []

    elements.append(_text("title", 80, 20,
                          "Step 5: Lexical (BM25) Index", size=24, family=2))
    elements.append(_text("cost", 80, 52,
                          "LOCAL \u00b7 FREE \u00b7 Rust-based Tantivy with Python bindings",
                          size=13, color=DARK_GREEN, family=2))

    y = 100

    # Input
    elements.extend(_labeled_box("chunks", 80, y, 180, 50, BLUE,
                                 "chunks table", "from Step 1",
                                 stroke=DARK_BLUE,
                                 bound_arrows=["a1"]))

    # Schema build
    elements.extend(_labeled_box("schema", 340, y, 220, 70, GREEN,
                                 "Tantivy Schema",
                                 'content: en_stem tokenizer\nfile_path, symbol: raw\nchunk_id: integer',
                                 bound_arrows=["a1", "a2"]))

    elements.append(_arrow("a1", 260, y + 25, 80, 0,
                           start_id="chunks", end_id="schema"))

    # Index writer
    y2 = y + 110
    elements.extend(_labeled_box("writer", 340, y2, 220, 60, GREEN,
                                 "Index Writer",
                                 "50MB heap \u00b7 add all docs\ncommit + merge threads",
                                 bound_arrows=["a2", "a3"]))

    elements.append(_arrow("a2", 450, y + 70, 0, y2 - y - 70,
                           start_id="schema", end_id="writer"))

    # Output
    y3 = y2 + 100
    elements.extend(_labeled_box("idx", 340, y3, 220, 50, BLUE,
                                 "Tantivy Index", "BM25 on disk",
                                 stroke=DARK_BLUE,
                                 bound_arrows=["a3"]))

    elements.append(_arrow("a3", 450, y2 + 60, 0, y3 - y2 - 60,
                           start_id="writer", end_id="idx"))

    # Rebuild note
    elements.extend(_labeled_box("note", 620, y2, 170, 60, "#ffe3e3",
                                 "Full Rebuild",
                                 "wipes + recreates\non every run",
                                 stroke="#c92a2a"))
    elements.append(_arrow("a-note", 560, y2 + 30, 60, 0,
                           start_id="writer", end_id="note"))

    # Usage
    y4 = y3 + 70
    elements.extend(_labeled_box("usage", 340, y4, 220, 50, "#fff3bf",
                                 "Powers: search_code tool",
                                 "exact match for identifiers",
                                 stroke=DARK_ORANGE))

    # Details
    y5 = y4 + 70
    details = [
        "Catches exact identifiers (variable names, error codes) that semantic search misses",
        "Tokenizer: English stemming on content, raw storage for paths/symbols",
        "Used in hybrid mode: BM25 scores combined with vector similarity",
        "No resume: always rebuilds from scratch (fast enough to not matter)",
    ]
    for i, d in enumerate(details):
        elements.append(_text(f"d-{i}", 80, y5 + i * 22,
                              d, size=12, color=MUTED, family=3, align="left"))

    return _make_file(elements)


# ─── Query-Time Flow ─────────────────────────────────────────────────────

PURPLE = "#d0bfff"      # LLM / agent components
DARK_PURPLE = "#7048e8"

def build_query_flow() -> dict:
    _reset_ids()
    elements = []

    # Title
    elements.append(_text("title", 80, 20,
                          "Query-Time Flow", size=28, family=2))
    elements.append(_text("subtitle", 80, 56,
                          "POST /query \u2192 agent loop \u2192 answer + evidence",
                          size=14, color=MUTED, family=2, align="left"))

    # ── Row 1: HTTP request flow ──
    y1 = 100
    elements.extend(_labeled_box("http", 80, y1, 180, 50, GRAY,
                                 'POST /query',
                                 '{"prompt": "..."}',
                                 bound_arrows=["a-http-api"]))

    elements.extend(_labeled_box("api", 340, y1, 180, 50, GREEN,
                                 "FastAPI Server",
                                 "indiseek.api.server",
                                 bound_arrows=["a-http-api", "a-api-loop"]))

    elements.extend(_labeled_box("loop", 600, y1, 200, 50, PURPLE,
                                 "AgentLoop.run()",
                                 "up to 15 iterations",
                                 stroke=DARK_PURPLE,
                                 bound_arrows=["a-api-loop", "a-loop-gemini"]))

    elements.append(_arrow("a-http-api", 260, y1 + 25, 80, 0,
                           start_id="http", end_id="api"))
    elements.append(_arrow("a-api-loop", 520, y1 + 25, 80, 0,
                           start_id="api", end_id="loop"))

    # ── Row 2: Gemini (the orchestrator brain) ──
    y2 = 200
    gemini_x = 300
    gemini_w = 360
    elements.extend(_labeled_box("gemini", gemini_x, y2, gemini_w, 80, ORANGE,
                                 "Gemini 2.0 Flash",
                                 "System prompt: research strategy\n"
                                 "Decides which tool to call next",
                                 bound_arrows=[
                                     "a-loop-gemini",
                                     "a-gemini-t1", "a-gemini-t2",
                                     "a-gemini-t3", "a-gemini-t4",
                                     "a-feedback",
                                     "a-gemini-resp",
                                 ]))

    elements.append(_arrow("a-loop-gemini", 700, y1 + 50, -220, y2 - y1 - 50,
                           start_id="loop", end_id="gemini"))

    # "tool calls" label on left
    elements.append(_text("lbl-calls", gemini_x - 10, y2 + 85,
                          "tool calls \u25bc", size=12, color=MUTED, family=3,
                          align="left"))

    # ── Row 3: Four tools ──
    y3 = 340
    tw = 160  # tool box width
    th = 70   # tool box height
    tool_gap = 30
    tools_start_x = 60

    tool_defs = [
        {"id": "t1", "label": "read_map",
         "sub": "directory tree\n+ file summaries", "x": tools_start_x},
        {"id": "t2", "label": "search_code",
         "sub": "hybrid semantic\n+ lexical search", "x": tools_start_x + tw + tool_gap},
        {"id": "t3", "label": "resolve_symbol",
         "sub": "definition, refs\ncallers, callees", "x": tools_start_x + 2 * (tw + tool_gap)},
        {"id": "t4", "label": "read_file",
         "sub": "source code\nwith line numbers", "x": tools_start_x + 3 * (tw + tool_gap)},
    ]

    for i, t in enumerate(tool_defs):
        tid = t["id"]
        elements.extend(_labeled_box(
            tid, t["x"], y3, tw, th, GREEN,
            t["label"], t["sub"],
            font_size=14,
            bound_arrows=[f"a-gemini-{tid}", f"a-{tid}-db"],
        ))
        # Arrow from Gemini down to tool
        gem_out_x = gemini_x + (i + 1) * gemini_w / 5
        tool_in_x = t["x"] + tw / 2
        elements.append(_arrow(
            f"a-gemini-{tid}", gem_out_x, y2 + 80,
            tool_in_x - gem_out_x, y3 - y2 - 80,
            start_id="gemini", end_id=tid,
        ))

    # ── Feedback arrow: tools → Gemini (right side, goes up) ──
    # Multi-segment arrow: from right of tool row, up and left back to Gemini
    fb_x = tools_start_x + 4 * (tw + tool_gap) - tool_gap + 20
    fb_y = y3 + th / 2
    fb_dx = gemini_x + gemini_w - fb_x + 30
    fb_dy = y2 + 40 - fb_y

    elements.append({
        "id": "a-feedback", "type": "arrow",
        "x": fb_x, "y": fb_y,
        "width": abs(fb_dx) + 30, "height": abs(fb_dy),
        "points": [[0, 0], [30, 0], [30, fb_dy], [fb_dx, fb_dy]],
        "startArrowhead": None, "endArrowhead": "arrow",
        "strokeColor": DARK_PURPLE, "strokeWidth": 2, "roughness": 1,
        "opacity": 100, "angle": 0, "groupIds": [],
        "isDeleted": False, "boundElements": [],
        "endBinding": {"elementId": "gemini", "focus": 0, "gap": 5},
    })

    # "tool results" label on the feedback arrow
    elements.append(_text("lbl-results", fb_x + 35, fb_y - 70,
                          "\u25b2 tool results\n(fed back to context)",
                          size=12, color=DARK_PURPLE, family=3, align="left"))

    # ── Row 4: Data stores / indexes ──
    y4 = 470
    dw = 160
    dh = 60

    store_defs = [
        {"id": "db1", "label": "SQLite",
         "sub": "file_summaries\n(from Step 4)", "x": tools_start_x,
         "source": "t1"},
        {"id": "db2", "label": "LanceDB + Tantivy",
         "sub": "vectors + BM25\n(Steps 3 & 5)", "x": tools_start_x + tw + tool_gap,
         "source": "t2"},
        {"id": "db3", "label": "SQLite",
         "sub": "SCIP xrefs + symbols\n(Steps 1 & 2)", "x": tools_start_x + 2 * (tw + tool_gap),
         "source": "t3"},
        {"id": "db4", "label": "Filesystem",
         "sub": "cloned repo\n(raw .ts/.tsx)", "x": tools_start_x + 3 * (tw + tool_gap),
         "source": "t4"},
    ]

    for sd in store_defs:
        elements.extend(_labeled_box(
            sd["id"], sd["x"], y4, dw, dh, BLUE,
            sd["label"], sd["sub"],
            stroke=DARK_BLUE, font_size=14,
            bound_arrows=[f"a-{sd['source']}-db"],
        ))
        # Arrow from tool to data store
        cx = sd["x"] + dw / 2
        elements.append(_arrow(
            f"a-{sd['source']}-db", cx, y3 + th, 0, y4 - y3 - th,
            start_id=sd["source"], end_id=sd["id"],
        ))

    # ── Embedding API call annotation (search_code needs to embed the query) ──
    embed_x = tools_start_x + tw + tool_gap + dw + 15
    embed_y = y3 + th + 10
    elements.extend(_labeled_box("embed-note", embed_x, embed_y, 140, 40, ORANGE,
                                 "Embed query",
                                 "Gemini Embedding API",
                                 font_size=11, stroke=DARK_ORANGE,
                                 bound_arrows=["a-embed"]))
    elements.append(_arrow("a-embed",
                           tools_start_x + tw + tool_gap + tw, y3 + th / 2 + 10,
                           embed_x - (tools_start_x + tw + tool_gap + tw), embed_y + 20 - y3 - th / 2 - 10,
                           start_id="t2", end_id="embed-note"))

    # ── Row 5: Response output ──
    y5 = 580
    elements.extend(_labeled_box("resp", 300, y5, 360, 60, "#d3f9d8",
                                 "JSON Response",
                                 '{"answer": "...", "evidence": [...]}',
                                 stroke=DARK_GREEN,
                                 bound_arrows=["a-gemini-resp"]))

    # Arrow from Gemini down to response (when model returns text, loop ends)
    elements.append(_arrow("a-gemini-resp", gemini_x + 20, y2 + 80,
                           300 - gemini_x, y5 - y2 - 80,
                           start_id="gemini", end_id="resp"))

    elements.append(_text("lbl-done", gemini_x - 50, y2 + 100,
                          "when model returns\ntext (no tool calls)\n\u2192 loop ends",
                          size=12, color=MUTED, family=3, align="left"))

    # ── Detail annotations ──
    y6 = y5 + 80
    details = [
        "Each iteration: Gemini decides \u2192 call tool(s) \u2192 results fed back as context \u2192 Gemini decides again",
        "Tool results truncated to 15k chars to stay within context window limits",
        "search_code uses Reciprocal Rank Fusion (RRF) to merge semantic + lexical results",
        "resolve_symbol tries SCIP cross-references first, falls back to tree-sitter symbols",
        "Cost per query: Gemini 2.0 Flash usage (~few thousand tokens) + 1 embedding call per search",
    ]
    for i, d in enumerate(details):
        elements.append(_text(f"detail-{i}", 60, y6 + i * 22,
                              d, size=12, color=MUTED, family=3, align="left"))

    return _make_file(elements)


# ─── Data Formats Diagram ─────────────────────────────────────────────

# Additional colors for data format cards
CARD_BG = "#f8f9fa"
INFO_HDR = "#495057"
SQLITE_HDR = "#1971c2"
LANCE_HDR = "#e67700"
TANTIVY_HDR = "#2b8a3e"
WARN_BG = "#fff3bf"

_LINE_H = 17  # approx line height at size=11


def _card(
    base_id: str, x: float, y: float, w: float,
    title: str, body_lines: list[str], hdr_color: str,
) -> tuple[list, float]:
    """Render a table card: colored header bar + light-gray body with monospace text.

    Returns (elements, total_height).
    """
    els: list = []
    hdr_h = 28
    body_h = len(body_lines) * _LINE_H + 16
    total_h = hdr_h + body_h

    # Header
    els.append(_rect(f"{base_id}-hdr", x, y, w, hdr_h, hdr_color, stroke=hdr_color))
    els.append(_text(f"{base_id}-title", x + 10, y + 5, title,
                     size=13, color=WHITE, family=2, align="left"))

    # Body
    els.append(_rect(f"{base_id}-body", x, y + hdr_h, w, body_h, CARD_BG, stroke="#dee2e6"))
    body_text = "\n".join(body_lines)
    els.append(_text(f"{base_id}-text", x + 10, y + hdr_h + 8, body_text,
                     size=11, color=DARK, family=3, align="left"))

    return els, total_h


def build_data_formats() -> dict:
    """Diagram showing what each record looks like in SQLite, LanceDB, and Tantivy."""
    _reset_ids()
    elements: list = []

    # ── Layout constants ──
    COL1 = 60       # SQLite
    COL2 = 510      # LanceDB
    COL3 = 880      # Tantivy
    CW1 = 430       # SQLite width (widest — most tables)
    CW2 = 350       # LanceDB width
    CW3 = 350       # Tantivy width
    GAP = 14        # gap between cards

    # ── Title ──
    elements.append(_text("title", 60, 20,
                          "Data Store Formats", size=28, family=2))
    elements.append(_text("subtitle", 60, 56,
                          "What each record looks like, with a concrete example from a Vite codebase",
                          size=14, color=MUTED, family=2, align="left"))

    # ── Source code example ──
    src_y = 90
    elements.append(_rect("src-bg", 60, src_y, 720, 145, GRAY, stroke="#adb5bd"))
    elements.append(_text("src-hdr", 70, src_y + 8,
                          "INPUT — packages/vite/src/node/server/index.ts (lines 45-50)",
                          size=12, color="#495057", family=2, align="left"))
    elements.append(_text("src-code", 70, src_y + 30,
                          'export function createServer(\n'
                          '  config: ServerConfig\n'
                          '): Server {\n'
                          '  const server = new HttpServer(config.port)\n'
                          '  return server\n'
                          '}',
                          size=13, color=DARK, family=3, align="left"))

    # ── Transformation labels (between source and columns) ──
    ty = 248
    elements.append(_text("t1", COL1, ty,
                          "Tree-sitter AST parse + extract",
                          size=11, color=DARK_GREEN, family=3, align="left"))
    elements.append(_text("t2", COL2, ty,
                          "chunks.content → Gemini embed API",
                          size=11, color=DARK_ORANGE, family=3, align="left"))
    elements.append(_text("t3", COL3, ty,
                          "chunks.content → en_stem tokenizer",
                          size=11, color=DARK_GREEN, family=3, align="left"))

    col_y = 275

    # ═══════════════════════════════════════════════════════════════════
    # SQLITE COLUMN
    # ═══════════════════════════════════════════════════════════════════
    elements.append(_rect("sql-hdr", COL1, col_y, CW1, 32, SQLITE_HDR, stroke=SQLITE_HDR))
    elements.append(_text("sql-title", COL1 + 10, col_y + 7,
                          "SQLite (indiseek.db) — 6 tables",
                          size=15, color=WHITE, family=2, align="left"))

    sy = col_y + 42

    # ─ symbols ─
    els, h = _card("sym", COL1, sy, CW1, "symbols — per-symbol metadata", [
        'id:         42',
        'file_path:  "packages/vite/src/node/server/index.ts"',
        'name:       "createServer"',
        'kind:       "function"',
        'start_line: 45   end_line: 48   (1-based)',
        'signature:  "export function createServer(con..."',
        '',
        'kinds: function | class | method | interface',
        '       type | enum | variable',
    ], SQLITE_HDR)
    elements.extend(els)
    sy += h + GAP

    # ─ chunks (THE KEY TABLE) ─
    els, h = _card("chk", COL1, sy, CW1,
                   "chunks — AST-scoped code slices  (KEY TABLE)", [
        'id:             137   ← JOIN KEY to LanceDB & Tantivy',
        'file_path:      "packages/vite/src/node/server/index.ts"',
        'symbol_name:    "createServer"   (NULL for module chunks)',
        'chunk_type:     "function"',
        'start_line:     45',
        'end_line:       48',
        'content:        "export function createServer(\\n"',
        '                "  config: ServerConfig\\n): Server {\\n"',
        '                "  const server = new HttpServer(..."',
        'token_estimate: 32   (= len(content) // 4)',
        '',
        'One chunk per top-level symbol in the file.',
        'If file has zero symbols → one "module" chunk = full file.',
    ], SQLITE_HDR)
    elements.extend(els)
    sy += h + GAP

    # ─ file_summaries ─
    els, h = _card("fsum", COL1, sy, CW1,
                   "file_summaries — LLM-generated one-liners", [
        'file_path:  "packages/vite/src/node/server/index.ts"',
        'summary:    "Creates and configures the Vite dev server"',
        '            "with HMR and middleware support."',
        'language:   "ts"',
        'line_count: 425',
        '',
        'Generated by: gemini-2.0-flash  (1 call per file)',
        'Input: file content truncated to 30k chars',
        'Prompt: "Summarize responsibility in one sentence"',
    ], SQLITE_HDR)
    elements.extend(els)
    sy += h + GAP

    # ─ scip_symbols ─
    els, h = _card("ssym", COL1, sy, CW1,
                   "scip_symbols — SCIP cross-ref identifiers", [
        'id:            7',
        'symbol:        "npm vite 5.0.0 src/`createServer`()."',
        'documentation: "Create a Vite development server."',
        '',
        'Local symbols (function-scoped) filtered out.',
        'Only globally-resolvable symbols stored.',
    ], SQLITE_HDR)
    elements.extend(els)
    sy += h + GAP

    # ─ scip_occurrences ─
    els, h = _card("socc", COL1, sy, CW1,
                   "scip_occurrences — where each symbol appears", [
        'symbol_id:  7   → scip_symbols.id',
        'file_path:  "src/node/server/index.ts"',
        'start_line: 44   start_col: 16   (0-BASED!)',
        'end_line:   44   end_col:   28',
        'role:       "definition"   (or "reference")',
        '',
        'Warning: lines are 0-based (SCIP convention),',
        'unlike symbols/chunks which are 1-based.',
    ], SQLITE_HDR)
    elements.extend(els)
    sy += h + GAP

    # ─ scip_relationships ─
    els, h = _card("srel", COL1, sy, CW1,
                   "scip_relationships — symbol connections", [
        'symbol_id:         7   → scip_symbols.id',
        'related_symbol_id: 12  → scip_symbols.id',
        'relationship:      "implementation"',
        '',
        'Types: implementation | type_definition',
        '       reference | definition',
    ], SQLITE_HDR)
    elements.extend(els)
    sql_bottom = sy + h

    # ═══════════════════════════════════════════════════════════════════
    # LANCEDB COLUMN
    # ═══════════════════════════════════════════════════════════════════
    elements.append(_rect("lance-hdr", COL2, col_y, CW2, 32,
                          LANCE_HDR, stroke=LANCE_HDR))
    elements.append(_text("lance-title", COL2 + 10, col_y + 7,
                          "LanceDB (vector store on disk)",
                          size=15, color=WHITE, family=2, align="left"))

    ly = col_y + 42

    # ─ chunks vector table ─
    els, h = _card("lchk", COL2, ly, CW2,
                   'Table "chunks" — embeddings + metadata', [
        'vector:      [0.023, -0.041, 0.117,',
        '              0.089, -0.156, 0.034,',
        '              ...  768 float32 values  ]',
        '',
        'chunk_id:    137   → SQLite chunks.id',
        'file_path:   "packages/vite/src/.../index.ts"',
        'symbol_name: "createServer"',
        '             ("" when NULL — no nullable fields)',
        'chunk_type:  "function"',
        'content:     "export function createServer(..."',
        '             (full source text, same as SQLite)',
    ], LANCE_HDR)
    elements.extend(els)
    ly += h + GAP

    # ─ How vectors are created ─
    els, h = _card("lhow", COL2, ly, CW2,
                   "How: source text → vector", [
        '1. Read chunks.content from SQLite',
        '2. Group into batches of 20 chunks',
        '3. Send to Gemini Embedding API:',
        '   model:  gemini-embedding-001',
        '   input:  raw content (no prefix/format)',
        '   output: 768-dim float32 vector',
        '4. Store vector + chunk metadata in LanceDB',
        '',
        'Resume: already-embedded chunk_ids are skipped.',
        'Retry: 1 retry/batch, 3 consecutive fails = abort.',
    ], INFO_HDR)
    elements.extend(els)
    ly += h + GAP

    # ─ Search behavior ─
    els, h = _card("lsearch", COL2, ly, CW2,
                   "At query time: cosine similarity", [
        'Input: "HMR propagation"',
        '  1. Embed query with same API/model',
        '  2. Cosine distance search in LanceDB',
        '  3. Returns: chunk_id, file_path,',
        '     symbol_name, content, score',
        '',
        'Lower _distance = more similar.',
        'Score inverted for ranking.',
    ], INFO_HDR)
    elements.extend(els)
    lance_bottom = ly + h

    # ═══════════════════════════════════════════════════════════════════
    # TANTIVY COLUMN
    # ═══════════════════════════════════════════════════════════════════
    elements.append(_rect("tan-hdr", COL3, col_y, CW3, 32,
                          TANTIVY_HDR, stroke=TANTIVY_HDR))
    elements.append(_text("tan-title", COL3 + 10, col_y + 7,
                          "Tantivy (BM25 full-text index)",
                          size=15, color=WHITE, family=2, align="left"))

    tty = col_y + 42

    # ─ BM25 document ─
    els, h = _card("tdoc", COL3, tty, CW3,
                   "BM25 Document — one per chunk", [
        'chunk_id:    137   → SQLite chunks.id',
        '',
        'content:     "export function createServer..."',
        '  tokenizer: en_stem (English stemming)',
        '  tokens:    ["export", "function",',
        '              "createserv", "config",',
        '              "serverconfig", "server",',
        '              "httpserver", "port", ...]',
        '',
        'file_path:   ".../index.ts"  (raw, no tokenize)',
        'symbol_name: "createServer"  (raw)',
        'chunk_type:  "function"      (raw)',
        'start_line:  45   (stored only, not indexed)',
        'end_line:    48   (stored only, not indexed)',
    ], TANTIVY_HDR)
    elements.extend(els)
    tty += h + GAP

    # ─ Tokenizer details ─
    els, h = _card("ttok", COL3, tty, CW3,
                   "How: en_stem tokenizer examples", [
        '"createServer"    → "createserv"',
        '"HttpServer"      → "httpserver"',
        '"configuration"   → "configur"',
        '"dependencies"    → "depend"',
        '',
        'Stemming enables partial-match queries.',
        '"configure" finds "configuration".',
        '',
        'raw fields (file_path, symbol_name):',
        '  stored as-is, exact match only.',
        '',
        'Queries parsed against content only.',
        'BM25 scoring: higher = more relevant.',
    ], INFO_HDR)
    elements.extend(els)
    tty += h + GAP

    # ─ Index lifecycle ─
    els, h = _card("tlife", COL3, tty, CW3,
                   "Index lifecycle", [
        'build_index():',
        '  1. rm -rf index directory',
        '  2. Read ALL chunks from SQLite',
        '  3. Write all docs in single pass',
        '  4. Commit + wait for merge threads',
        '',
        'No incremental updates — full rebuild.',
        'Fast enough it doesn\'t matter.',
        'Writer heap: 50MB.',
    ], INFO_HDR)
    elements.extend(els)
    tantivy_bottom = tty + h

    # ═══════════════════════════════════════════════════════════════════
    # JOIN KEY ANNOTATION (bottom)
    # ═══════════════════════════════════════════════════════════════════
    bottom_y = max(sql_bottom, lance_bottom, tantivy_bottom) + 30
    ann_w = COL3 + CW3 - COL1
    elements.append(_rect("join-bg", COL1, bottom_y, ann_w, 50,
                          WARN_BG, stroke=DARK_ORANGE))
    elements.append(_text("join-text", COL1 + 15, bottom_y + 8,
                          "JOIN KEY:  chunk_id  connects all three stores  →  "
                          "SQLite chunks.id is the primary key.\n"
                          "LanceDB and Tantivy both store chunk_id as a foreign key "
                          "to correlate search results back to SQLite.",
                          size=12, color=DARK, family=3, align="left"))

    return _make_file(elements)


# ─── Query Trace Diagram ─────────────────────────────────────────────────

RED_BG = "#ffc9c9"       # error steps
YELLOW_BG = "#ffec99"    # wasteful/redundant steps
GREEN_BG = "#b2f2bb"     # productive steps
PURPLE_BG = "#d0bfff"    # synthesis
DARK_RED = "#c92a2a"

def build_query_trace() -> dict:
    """Trace of a live query: 'How does Vite HMR propagation work when a CSS file changes?'

    Captured 2025-02-15. 13 tool calls across 13 iterations — produced answer.
    """
    _reset_ids()
    elements = []

    # ── Title & subtitle ──
    elements.append(_text("title", 30, 15,
        "Query Trace: 'How does Vite HMR propagation work when a CSS file changes?'",
        size=20, family=2, align="left"))
    elements.append(_text("subtitle", 30, 48,
        "Result: 13 tool calls across 13 iterations — ANSWER produced",
        size=15, color=DARK_GREEN, family=2, align="left"))

    # ── Legend ──
    leg_x, leg_y = 720, 15
    for i, (color, label) in enumerate([
        (GREEN_BG, "Productive"),
        (YELLOW_BG, "Redundant"),
        (PURPLE_BG, "Synthesis"),
    ]):
        ry = leg_y + i * 22
        elements.append(_rect(f"leg-r{i}", leg_x, ry, 16, 16, color))
        elements.append(_text(f"leg-t{i}", leg_x + 22, ry,
            label, size=13, family=2, align="left"))

    # ── Tool call steps ──
    # Each: (tool_call_text, color, annotation)
    steps = [
        ("search_code('CSS HMR propagation')",
         GREEN_BG, "Good initial search — 10 results"),
        ("read_file(css.ts, lines 1-540)",
         GREEN_BG, "Key CSS plugin file found"),
        ("search_code('handleHotUpdate packages/vite/.../css.ts')",
         YELLOW_BG, "File path in query — semantic search ignores it"),
        ("read_file(css.ts, lines 3000-3539)",
         GREEN_BG, "Reading cssPostPlugin section"),
        ("search_code('handleHotUpdate')",
         GREEN_BG, "Broader search finds HMR handler"),
        ("read_file(plugin.ts, lines 100-150)",
         GREEN_BG, "Understanding Plugin interface"),
        ("search_code('hotUpdate packages/vite/.../css.ts')",
         YELLOW_BG, "Redundant — variant of step 3"),
        ("read_file(css.ts, lines 1050-1150)",
         GREEN_BG, "Reading chunk handling section"),
        ("search_code('hotUpdate: packages/vite/.../css.ts')",
         YELLOW_BG, "Redundant — 3rd variant of same query"),
        ("search_code('Handle CSS @import dependency HMR...addWatchFile')",
         GREEN_BG, "Targeted search for @import tracking"),
        ("read_file(css.ts, lines 400-500)",
         GREEN_BG, "Reading @import handling code"),
        ("search_code('async handler(_, id) packages/vite/.../css.ts')",
         YELLOW_BG, "Redundant — file path in query again"),
        ("resolve_symbol('cssAnalysisPlugin', 'definition')",
         GREEN_BG, "SCIP lookup — finds definition site"),
    ]

    box_x = 55
    box_w = 520
    box_h = 36
    ann_x = 600
    row_gap = 46
    start_y = 100

    for i, (call_text, bg, annotation) in enumerate(steps):
        y = start_y + i * row_gap

        # Step number
        elements.append(_text(f"cn{i}", 10, y + 8,
            f"{i+1}.", size=14, family=2, color=MUTED, align="right"))

        # Call box
        elements.extend(_labeled_box(
            f"cr{i}", box_x, y, box_w, box_h, bg,
            call_text, font_size=13,
        ))

        # Annotation
        ann_color = MUTED
        if bg == YELLOW_BG:
            ann_color = DARK_ORANGE
        elif bg == RED_BG:
            ann_color = DARK_RED
        elements.append(_text(f"ca{i}", ann_x, y + 8,
            annotation, size=13, family=2, color=ann_color, align="left"))

    # ── Phase brackets on the right ──
    bracket_x = 1060

    # Discovery phase: steps 1-2
    disc_y1 = start_y
    disc_y2 = start_y + 1 * row_gap + box_h
    elements.append({
        "id": "pb-disc", "type": "line",
        "x": bracket_x, "y": disc_y1,
        "width": 0, "height": disc_y2 - disc_y1,
        "points": [[0, 0], [0, disc_y2 - disc_y1]],
        "strokeColor": DARK_GREEN, "strokeWidth": 2, "strokeStyle": "dashed",
        "roughness": 1, "opacity": 100, "angle": 0, "groupIds": [],
        "isDeleted": False, "boundElements": [],
        "startArrowhead": None, "endArrowhead": None,
    })
    elements.append(_text("pl-disc", bracket_x + 10,
        (disc_y1 + disc_y2) // 2 - 10,
        "Discovery", size=14, family=2, color=DARK_GREEN, align="left"))

    # Deep exploration: steps 3-12
    exp_y1 = start_y + 2 * row_gap
    exp_y2 = start_y + 11 * row_gap + box_h
    elements.append({
        "id": "pb-exp", "type": "line",
        "x": bracket_x, "y": exp_y1,
        "width": 0, "height": exp_y2 - exp_y1,
        "points": [[0, 0], [0, exp_y2 - exp_y1]],
        "strokeColor": DARK_ORANGE, "strokeWidth": 2, "strokeStyle": "dashed",
        "roughness": 1, "opacity": 100, "angle": 0, "groupIds": [],
        "isDeleted": False, "boundElements": [],
        "startArrowhead": None, "endArrowhead": None,
    })
    elements.append(_text("pl-exp", bracket_x + 10,
        (exp_y1 + exp_y2) // 2 - 18,
        "Deep exploration\n(some redundancy)",
        size=14, family=2, color=DARK_ORANGE, align="left"))

    # SCIP: step 13
    scip_y1 = start_y + 12 * row_gap
    scip_y2 = scip_y1 + box_h
    elements.append({
        "id": "pb-scip", "type": "line",
        "x": bracket_x, "y": scip_y1,
        "width": 0, "height": scip_y2 - scip_y1,
        "points": [[0, 0], [0, scip_y2 - scip_y1]],
        "strokeColor": DARK_PURPLE, "strokeWidth": 2, "strokeStyle": "dashed",
        "roughness": 1, "opacity": 100, "angle": 0, "groupIds": [],
        "isDeleted": False, "boundElements": [],
        "startArrowhead": None, "endArrowhead": None,
    })
    elements.append(_text("pl-scip", bracket_x + 10, scip_y1 + 5,
        "SCIP nav", size=14, family=2, color=DARK_PURPLE, align="left"))

    # ── Diagnosis box ──
    diag_y = start_y + 13 * row_gap + 20
    elements.append(_rect("diag-bg", 30, diag_y, 1160, 120,
        "#fff3bf", stroke=DARK_ORANGE))
    elements.append(_text("diag-title", 50, diag_y + 8,
        "DIAGNOSIS", size=18, family=2, color=DARK_ORANGE, align="left"))
    elements.append(_text("diag-body", 50, diag_y + 36,
        "\u2022 9/13 productive  |  4/13 redundant (file path in semantic query)  |  0 errors  |  ANSWER produced\n"
        "\u2022 Compared to previous trace: 13 calls vs 15 calls, answer vs no answer\n"
        "\u2022 Improvement: no path: filter errors (system prompt now warns against it)\n"
        "\u2022 Remaining issue: 4 redundant searches still include file paths in semantic queries",
        size=13, family=2, align="left"))

    # ── Comparison with old trace ──
    comp_y = diag_y + 140
    elements.append(_rect("comp-bg", 30, comp_y, 1160, 100,
        "#edf2ff", stroke="#5c7cfa"))
    elements.append(_text("comp-title", 50, comp_y + 8,
        "VS PREVIOUS TRACE (same query)", size=18, family=2,
        color="#5c7cfa", align="left"))
    elements.append(_text("comp-body", 50, comp_y + 36,
        "Before:  15 iterations, 15 tool calls, 4 path: errors, 0 synthesis  \u2192  NO ANSWER\n"
        "After:   13 iterations, 13 tool calls, 0 errors, answer produced  \u2192  ANSWER\n"
        "Delta:   path: filter errors eliminated, model converges to synthesis at iter 13",
        size=13, family=2, align="left"))

    return _make_file(elements)


def main():
    DIAGRAMS_DIR.mkdir(parents=True, exist_ok=True)

    diagrams = {
        "pipeline-overview.excalidraw": build_overview(),
        "step1-treesitter.excalidraw": build_step1_detail(),
        "step2-scip.excalidraw": build_step2_detail(),
        "step3-embedding.excalidraw": build_step3_detail(),
        "step4-summarization.excalidraw": build_step4_detail(),
        "step5-lexical.excalidraw": build_step5_detail(),
        "query-flow.excalidraw": build_query_flow(),
        "data-formats.excalidraw": build_data_formats(),
        "query-trace.excalidraw": build_query_trace(),
    }

    for name, data in diagrams.items():
        path = DIAGRAMS_DIR / name
        path.write_text(json.dumps(data, indent=2))
        print(f"  {path}")

    print(f"\nGenerated {len(diagrams)} diagrams in {DIAGRAMS_DIR}")


if __name__ == "__main__":
    main()
