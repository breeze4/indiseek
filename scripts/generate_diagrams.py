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
    }

    for name, data in diagrams.items():
        path = DIAGRAMS_DIR / name
        path.write_text(json.dumps(data, indent=2))
        print(f"  {path}")

    print(f"\nGenerated {len(diagrams)} diagrams in {DIAGRAMS_DIR}")


if __name__ == "__main__":
    main()
