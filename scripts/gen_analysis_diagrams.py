#!/usr/bin/env python3
"""Generate Excalidraw analysis diagrams for the agent loop."""

import json
import os

_seed = 1000


def _next_seed():
    global _seed
    _seed += 1
    return _seed


# ── Element factories ──────────────────────────────────────────────


def _base(id, typ, x, y, w, h, bg="transparent", stroke="#1e1e1e",
          stroke_w=2, stroke_style="solid", roundness_type=3):
    return {
        "id": id, "type": typ, "x": x, "y": y, "width": w, "height": h,
        "angle": 0, "strokeColor": stroke, "backgroundColor": bg,
        "fillStyle": "solid", "strokeWidth": stroke_w,
        "strokeStyle": stroke_style, "roughness": 1, "opacity": 100,
        "roundness": {"type": roundness_type} if roundness_type else None,
        "seed": _next_seed(), "version": 1, "versionNonce": _next_seed(),
        "isDeleted": False, "boundElements": [],
        "updated": 1739000000000, "link": None, "locked": False,
        "groupIds": [],
    }


def rect(id, x, y, w, h, bg="#a5d8ff", text_id=None, **kw):
    el = _base(id, "rectangle", x, y, w, h, bg, **kw)
    if text_id:
        el["boundElements"] = [{"id": text_id, "type": "text"}]
    return el


def diamond(id, x, y, w, h, bg="#ffec99", text_id=None):
    el = _base(id, "diamond", x, y, w, h, bg, roundness_type=2)
    if text_id:
        el["boundElements"] = [{"id": text_id, "type": "text"}]
    return el


def text(id, x, y, w, h, content, container_id=None, font_size=18,
         align="center", valign="middle", color="#1e1e1e"):
    el = _base(id, "text", x, y, w, h, stroke=color, stroke_w=0,
               roundness_type=None)
    el.update({
        "text": content, "fontSize": font_size, "fontFamily": 2,
        "textAlign": align, "verticalAlign": valign,
        "containerId": container_id, "originalText": content,
        "autoResize": True, "lineHeight": 1.25,
    })
    return el


def arrow(id, x, y, points, color="#1e1e1e", stroke_style="solid"):
    px = [p[0] for p in points]
    py = [p[1] for p in points]
    w = max(px) - min(px) if len(px) > 1 else 0
    h = max(py) - min(py) if len(py) > 1 else 0
    el = _base(id, "arrow", x, y, w, h, stroke=color,
               stroke_style=stroke_style, roundness_type=2)
    el.update({
        "points": points,
        "startArrowhead": None, "endArrowhead": "arrow",
        "startBinding": None, "endBinding": None,
    })
    return el


def line(id, x, y, points, color="#1e1e1e", stroke_style="dashed"):
    px = [p[0] for p in points]
    py = [p[1] for p in points]
    w = max(px) - min(px) if len(px) > 1 else 0
    h = max(py) - min(py) if len(py) > 1 else 0
    el = _base(id, "line", x, y, w, h, stroke=color,
               stroke_style=stroke_style, roundness_type=2)
    el.update({
        "points": points,
        "startArrowhead": None, "endArrowhead": None,
        "startBinding": None, "endBinding": None,
    })
    return el


def wrap(elements):
    return {
        "type": "excalidraw",
        "version": 2,
        "source": "indiseek-analysis",
        "elements": elements,
        "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
        "files": {},
    }


# ── Colors ─────────────────────────────────────────────────────────

BLUE = "#a5d8ff"
ORANGE = "#ffd8a8"
YELLOW = "#ffec99"
GREEN = "#b2f2bb"
RED = "#ffc9c9"
GRAY = "#e9ecef"
PURPLE = "#d0bfff"
ANN = "#495057"


# ── Diagram 1: Agent Loop Architecture ─────────────────────────────


def agent_loop_diagram():
    els = []
    cx = 500  # center x

    # Title
    els.append(text("title", 220, 10, 560, 35,
                    "Agent Loop Architecture", font_size=28, valign="top"))

    # ── Box 1: POST /query ──
    w1, h1 = 260, 50
    x1, y1 = cx - w1 // 2, 70
    els.append(rect("r1", x1, y1, w1, h1, BLUE, "t1"))
    els.append(text("t1", x1, y1, w1, h1, "POST /query",
                    container_id="r1", font_size=20))

    # ── Box 2: Build System Prompt ──
    w2, h2 = 360, 70
    x2, y2 = cx - w2 // 2, 170
    els.append(rect("r2", x2, y2, w2, h2, BLUE, "t2"))
    els.append(text("t2", x2, y2, w2, h2,
                    "Build System Prompt\n(bakes in full repo map from read_map)",
                    container_id="r2", font_size=14))

    # ── Box 3: LLM Call (expensive — highlighted) ──
    w3, h3 = 320, 64
    x3, y3 = cx - w3 // 2, 310
    els.append(rect("r3", x3, y3, w3, h3, ORANGE, "t3"))
    els.append(text("t3", x3, y3, w3, h3,
                    "Gemini generate_content()\n(conversation history + tool decls)",
                    container_id="r3", font_size=14))

    # ── Diamond 4: function_calls? ──
    wd, hd = 250, 130
    xd, yd = cx - wd // 2, 445
    els.append(diamond("d4", xd, yd, wd, hd, YELLOW, "t4"))
    els.append(text("t4", xd, yd, wd, hd,
                    "function_calls\nin response?",
                    container_id="d4", font_size=16))

    # ── Box 5: Return Answer (success — left) ──
    w5, h5 = 220, 56
    x5, y5 = 40, 475
    els.append(rect("r5", x5, y5, w5, h5, GREEN, "t5"))
    els.append(text("t5", x5, y5, w5, h5, "Return Answer",
                    container_id="r5", font_size=18))

    # ── Box 6: Execute Tool Batch ──
    w6, h6 = 320, 70
    x6, y6 = cx - w6 // 2, 650
    els.append(rect("r6", x6, y6, w6, h6, BLUE, "t6"))
    els.append(text("t6", x6, y6, w6, h6,
                    "Execute All Tool Calls\n(1-5 calls batched from single LLM turn)",
                    container_id="r6", font_size=13))

    # ── Box 7: Post-process ──
    w7, h7 = 340, 70
    x7, y7 = cx - w7 // 2, 790
    els.append(rect("r7", x7, y7, w7, h7, BLUE, "t7"))
    els.append(text("t7", x7, y7, w7, h7,
                    "Truncate each result to 15k chars\nAppend '[N remaining]' budget tag",
                    container_id="r7", font_size=13))

    # ── Diamond 8: iterations left? ──
    wd8, hd8 = 250, 130
    xd8, yd8 = cx - wd8 // 2, 930
    els.append(diamond("d8", xd8, yd8, wd8, hd8, YELLOW, "t8"))
    els.append(text("t8", xd8, yd8, wd8, hd8,
                    "iteration\n< MAX (15)?",
                    container_id="d8", font_size=16))

    # ── Box 9: Return Partial (failure — right) ──
    w9, h9 = 250, 56
    x9, y9 = 800, 965
    els.append(rect("r9", x9, y9, w9, h9, RED, "t9"))
    els.append(text("t9", x9, y9, w9, h9,
                    "Return Partial (FAILURE)\nmax iterations exhausted",
                    container_id="r9", font_size=13))

    # ── Arrows ──
    # 1 → 2
    els.append(arrow("a12", cx, y1 + h1, [[0, 0], [0, y2 - y1 - h1]]))
    # 2 → 3
    els.append(arrow("a23", cx, y2 + h2, [[0, 0], [0, y3 - y2 - h2]]))
    # 3 → d4
    els.append(arrow("a3d", cx, y3 + h3, [[0, 0], [0, yd - y3 - h3]]))
    # d4 → 5 (No — left)
    els.append(arrow("ad5", xd, yd + hd // 2,
                     [[0, 0], [-(xd - x5 - w5), y5 + h5 // 2 - yd - hd // 2]]))
    # d4 → 6 (Yes — down)
    els.append(arrow("ad6", cx, yd + hd, [[0, 0], [0, y6 - yd - hd]]))
    # 6 → 7
    els.append(arrow("a67", cx, y6 + h6, [[0, 0], [0, y7 - y6 - h6]]))
    # 7 → d8
    els.append(arrow("a7d", cx, y7 + h7, [[0, 0], [0, yd8 - y7 - h7]]))
    # d8 → 9 (No — right)
    els.append(arrow("ad9", xd8 + wd8, yd8 + hd8 // 2,
                     [[0, 0], [x9 - xd8 - wd8, y9 + h9 // 2 - yd8 - hd8 // 2]]))
    # d8 → 3 (Yes — loop back via left side)
    lx = x3 - 50  # left gutter
    ly3 = y3 + h3 // 2
    els.append(arrow("aloop", xd8, yd8 + hd8 // 2, [
        [0, 0],
        [lx - xd8, 0],
        [lx - xd8, ly3 - yd8 - hd8 // 2],
        [x3 - xd8, ly3 - yd8 - hd8 // 2],
    ]))

    # ── Arrow labels ──
    els.append(text("lno1", 265, 470, 60, 20, "No",
                    font_size=15, color="#2b8a3e"))
    els.append(text("lyes1", 510, 595, 50, 20, "Yes",
                    font_size=15, color="#e67700"))
    els.append(text("lno2", 720, 978, 60, 20, "No",
                    font_size=15, color="#c92a2a"))
    els.append(text("lyes2", 250, 920, 50, 20, "Yes",
                    font_size=15, color="#1971c2"))

    # ── Annotations (right side) ──
    ax = 760
    els.append(text("ann1", ax, 178, 300, 44,
                    "System prompt = repo map + strategy\n~8-15k chars before any tool output",
                    font_size=13, align="left", valign="top", color=ANN))
    els.append(text("ann2", ax, 318, 280, 44,
                    "2-5s per Gemini API call\nDominates total query latency",
                    font_size=13, align="left", valign="top", color=ANN))
    els.append(text("ann3", ax, 658, 280, 55,
                    "Tool dispatch (no parallelism):\n"
                    "  search_code  |  resolve_symbol\n"
                    "  read_file    |  read_map",
                    font_size=13, align="left", valign="top", color=ANN))
    els.append(text("ann4", ax, 798, 320, 55,
                    "BUG: says 'N tool calls remaining'\n"
                    "but N counts ITERATIONS, not calls.\n"
                    "5 tools in 1 iter still costs 1 'call'.",
                    font_size=13, align="left", valign="top", color="#c92a2a"))

    # ── Loop bracket (left dashed line) ──
    bx = 240
    els.append(line("lbr", bx, y3 - 5, [[0, 0], [0, yd8 + hd8 - y3 + 10]],
                    "#5c7cfa", "dashed"))
    els.append(text("lbr_lbl", 175, (y3 + yd8 + hd8) // 2 - 12, 60, 25,
                    "LOOP", font_size=18, color="#5c7cfa"))

    # ── Key problems callout (bottom) ──
    ky = 1100
    els.append(rect("kbg", 40, ky, 1020, 170, "#fff3bf",
                     stroke="#e67700", stroke_style="dashed"))
    els.append(text("ktitle", 60, ky + 10, 400, 25,
                    "KEY PROBLEMS OBSERVED",
                    font_size=18, align="left", valign="top", color="#e67700"))
    els.append(text("kbody", 60, ky + 40, 980, 120,
                    "1. No tool documentation in prompt → model invents syntax (path: filters)\n"
                    "2. Budget tag says 'tool calls remaining' but counts iterations (misleading)\n"
                    "3. 'Synthesize now' nudge only at remaining ≤ 2 — too late, model already committed\n"
                    "4. No error recovery — model repeats failed pattern: syntax 4 times in a row\n"
                    "5. No convergence pressure — model explores indefinitely instead of answering",
                    font_size=14, align="left", valign="top", color="#1e1e1e"))

    return wrap(els)


# ── Diagram 2: Query Execution Trace ───────────────────────────────


def query_trace_diagram():
    els = []

    # Title
    els.append(text("title", 30, 15, 950, 28,
                    "Query Trace: 'How does Vite HMR propagation work when a CSS file changes?'",
                    font_size=20, align="left", valign="top"))
    els.append(text("subtitle", 30, 48, 600, 22,
                    "Result: 15 tool calls across 15 iterations — NO ANSWER produced",
                    font_size=15, align="left", valign="top", color="#c92a2a"))

    # Legend
    lx = 700
    for i, (color, label) in enumerate([
        (GREEN, "Productive"),
        (YELLOW, "Wasteful / redundant"),
        (RED, "Error"),
    ]):
        ly = 15 + i * 22
        els.append(rect(f"leg_r{i}", lx, ly, 16, 16, color))
        els.append(text(f"leg_t{i}", lx + 22, ly, 120, 16, label,
                        font_size=13, align="left", valign="top"))

    # ── Tool call rows ──

    calls = [
        ("search_code('HMR CSS change propagation')",        GREEN,  "Good initial search"),
        ("read_file(hmr.ts)",                                GREEN,  "Key HMR file found"),
        ("search_code('css')",                               YELLOW, "Way too broad"),
        ("read_file(hmr.ts:800-1100)",                       YELLOW, "Re-reading same file"),
        ("search_code('HMR path:...css.ts')",                RED,    "ERROR: path: filter unsupported"),
        ("read_file(css.ts)",                                GREEN,  ""),
        ("search_code('import.meta.hot path:...css.ts')",    RED,    "ERROR: same path: mistake"),
        ("search_code('import.meta.hot.accept')",            GREEN,  ""),
        ("read_file(css.ts:2300-2600)",                      GREEN,  ""),
        ("search_code('updateStyle')",                       GREEN,  ""),
        ("search_code('updateStyle path:...css.ts')",        RED,    "ERROR: 3rd path: attempt"),
        ("read_file(css.ts:800-1100)",                       YELLOW, "Redundant — already read"),
        ("search_code('\"vite:css\"')",                      GREEN,  ""),
        ("search_code('updateStyle path:...css.ts')",        RED,    "ERROR: 4th path: attempt"),
        ("resolve_symbol('cssPlugin', 'definition')",        GREEN,  "Good but too late"),
    ]

    rh = 40       # row height
    rg = 6        # row gap
    sy = 100      # start y
    bx = 55       # box x
    bw = 490      # box width
    ax = 570      # annotation x

    for i, (desc, color, note) in enumerate(calls):
        y = sy + i * (rh + rg)
        rid = f"cr{i}"
        tid = f"ct{i}"

        # Row number
        els.append(text(f"cn{i}", 10, y + 8, 38, 20, f"{i + 1}.",
                        font_size=14, align="right", valign="top", color=ANN))

        # Tool call box
        els.append(rect(rid, bx, y, bw, rh, color, tid))
        els.append(text(tid, bx, y, bw, rh, desc,
                        container_id=rid, font_size=13, align="left"))

        # Annotation
        if note:
            nc = "#c92a2a" if "ERROR" in note else "#868e96"
            els.append(text(f"ca{i}", ax, y + 8, 380, 20, note,
                            font_size=13, align="left", valign="top", color=nc))

    # ── Phase brackets (right side) ──

    # Phase 1: Discovery (calls 1-2)
    py1 = sy
    ph1 = 2 * (rh + rg) - rg
    els.append(line("pb1", 960, py1, [[0, 0], [0, ph1]], "#2b8a3e"))
    els.append(text("pl1", 970, py1 + ph1 // 2 - 10, 130, 20,
                    "Discovery",
                    font_size=14, align="left", valign="top", color="#2b8a3e"))

    # Phase 2: Exploration spiral (calls 3-15)
    py2 = sy + 2 * (rh + rg)
    ph2 = 13 * (rh + rg) - rg
    els.append(line("pb2", 960, py2, [[0, 0], [0, ph2]], "#c92a2a"))
    els.append(text("pl2", 970, py2 + ph2 // 2 - 20, 200, 44,
                    "Exploration spiral\n(never converges to synthesis)",
                    font_size=14, align="left", valign="top", color="#c92a2a"))

    # ── Ideal flow comparison ──

    iy = sy + 15 * (rh + rg) + 30
    els.append(rect("ideal_bg", 30, iy, 1060, 200, "#edf2ff",
                    stroke="#5c7cfa", stroke_style="dashed"))
    els.append(text("ideal_title", 50, iy + 10, 500, 25,
                    "IDEAL EXECUTION (same query, 3 iterations)",
                    font_size=18, align="left", valign="top", color="#5c7cfa"))

    ideal_calls = [
        ("Iter 1", "search_code('HMR CSS propagation') + resolve_symbol('handleHMRUpdate', 'definition')", GREEN),
        ("Iter 2", "read_file(hmr.ts, key lines) + read_file(css.ts, key lines)", GREEN),
        ("Iter 3", "SYNTHESIZE ANSWER from collected evidence", PURPLE),
    ]
    for j, (label, desc, color) in enumerate(ideal_calls):
        jy = iy + 45 + j * 46
        els.append(text(f"il{j}", 55, jy + 5, 60, 20, label,
                        font_size=14, align="right", valign="top", color="#5c7cfa"))
        els.append(rect(f"ir{j}", 125, jy, 520, 36, color, f"it{j}"))
        els.append(text(f"it{j}", 125, jy, 520, 36, desc,
                        container_id=f"ir{j}", font_size=13, align="left"))

    els.append(text("ideal_note", 680, iy + 55, 380, 66,
                    "3 iterations, 5 tool calls\n"
                    "~15s total (vs 15 iters, ~75s, no answer)\n"
                    "Budget: 20% used, 80% headroom",
                    font_size=14, align="left", valign="top", color="#5c7cfa"))

    # ── Summary statistics ──

    sty = iy + 220
    els.append(rect("sum_bg", 30, sty, 1060, 130, "#fff3bf",
                    stroke="#e67700", stroke_style="dashed"))
    els.append(text("sum_title", 50, sty + 8, 200, 22,
                    "DIAGNOSIS",
                    font_size=18, align="left", valign="top", color="#e67700"))
    els.append(text("sum_body", 50, sty + 38, 1020, 80,
                    "• 7/15 productive  |  4/15 errors (path: filter)  |  "
                    "2/15 redundant reads  |  2/15 unfocused  |  0/15 synthesis\n"
                    "• Model had enough context by call #6 to answer the question\n"
                    "• path: filter error repeated 4 times — no learning from tool errors\n"
                    "• 'Synthesize now' message only appears at iteration 13 of 15 — far too late",
                    font_size=13, align="left", valign="top"))

    return wrap(els)


# ── Diagram 3: Proposed Architecture ───────────────────────────────


def proposed_architecture_diagram():
    """Shows the proposed phased approach vs current."""
    els = []

    # Title
    els.append(text("title", 50, 10, 700, 35,
                    "Proposed: Phased Agent Loop",
                    font_size=28, align="left", valign="top"))
    els.append(text("subtitle", 50, 48, 800, 22,
                    "Same 15-iteration budget, structured into research → synthesis phases",
                    font_size=15, align="left", valign="top", color=ANN))

    # ── Current (left column) ──

    col1x = 60
    els.append(text("cur_h", col1x, 100, 400, 25,
                    "CURRENT: Unstructured Loop",
                    font_size=20, align="left", valign="top", color="#c92a2a"))

    current_rows = [
        ("Iter 1-15: LLM decides freely", GRAY),
        ("No phase boundaries", GRAY),
        ("'Synthesize' nudge at iter 13", GRAY),
        ("Model invents tool syntax", RED),
        ("Repeats errors without learning", RED),
        ("May never synthesize answer", RED),
    ]
    for i, (label, color) in enumerate(current_rows):
        ry = 140 + i * 46
        els.append(rect(f"cur_r{i}", col1x, ry, 420, 38, color, f"cur_t{i}"))
        els.append(text(f"cur_t{i}", col1x, ry, 420, 38, label,
                        container_id=f"cur_r{i}", font_size=14, align="left"))

    # ── Proposed (right column) ──

    col2x = 560
    els.append(text("new_h", col2x, 100, 400, 25,
                    "PROPOSED: Phased Approach",
                    font_size=20, align="left", valign="top", color="#2b8a3e"))

    # Phase 1: Research
    p1y = 140
    els.append(rect("p1_bg", col2x, p1y, 460, 160, "#edf2ff",
                    stroke="#5c7cfa"))
    els.append(text("p1_title", col2x + 15, p1y + 8, 200, 22,
                    "Phase 1: Research (iter 1-10)",
                    font_size=16, align="left", valign="top", color="#5c7cfa"))
    els.append(text("p1_body", col2x + 15, p1y + 35, 430, 110,
                    "• search_code, resolve_symbol, read_file\n"
                    "• Tool docs with examples in prompt\n"
                    "• Error feedback: 'path: is not supported,\n"
                    "  use read_file to scope by file'\n"
                    "• Convergence nudge at iter 7:\n"
                    "  'Start wrapping up research'",
                    font_size=13, align="left", valign="top"))

    # Phase 2: Synthesis
    p2y = 320
    els.append(rect("p2_bg", col2x, p2y, 460, 100, "#ebfbee",
                    stroke="#2b8a3e"))
    els.append(text("p2_title", col2x + 15, p2y + 8, 250, 22,
                    "Phase 2: Synthesis (iter 11-15)",
                    font_size=16, align="left", valign="top", color="#2b8a3e"))
    els.append(text("p2_body", col2x + 15, p2y + 35, 430, 55,
                    "• tool_config mode='NONE' — force text answer\n"
                    "• All evidence already in context\n"
                    "• Model must synthesize, cannot explore more",
                    font_size=13, align="left", valign="top"))

    # Arrow between phases
    els.append(arrow("p_arr", col2x + 230, p1y + 160,
                     [[0, 0], [0, p2y - p1y - 160]]))

    # ── Key changes list ──

    ky = 460
    els.append(rect("fix_bg", 40, ky, 1000, 250, "#fff3bf",
                    stroke="#e67700", stroke_style="dashed"))
    els.append(text("fix_title", 60, ky + 10, 400, 25,
                    "CONCRETE FIXES",
                    font_size=20, align="left", valign="top", color="#e67700"))

    fixes = [
        "1. Add tool usage docs to system prompt — search_code supports query + mode only, not field filters",
        "2. Add search_code examples: good ('HMR CSS propagation') vs bad ('HMR path:foo.ts')",
        "3. Fix budget message: '[N iterations left, M tool calls used]' — accurate accounting",
        "4. Earlier convergence nudge: at 50% budget say 'wrap up research soon'",
        "5. Hard synthesis phase: last 3-5 iterations → tool_config mode='NONE'",
        "6. Smarter error feedback: on tool error, append fix suggestion ('use read_file to scope to a file')",
        "7. Track tool call count separately from iteration count in the budget",
    ]
    els.append(text("fix_body", 60, ky + 42, 960, len(fixes) * 22,
                    "\n".join(fixes),
                    font_size=13, align="left", valign="top"))

    return wrap(els)


# ── Main ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    outdir = os.path.join(os.path.dirname(__file__), "..", "docs", "diagrams")
    os.makedirs(outdir, exist_ok=True)

    diagrams = {
        "agent-loop.excalidraw": agent_loop_diagram(),
        "query-trace.excalidraw": query_trace_diagram(),
        "proposed-fixes.excalidraw": proposed_architecture_diagram(),
    }

    for name, data in diagrams.items():
        path = os.path.join(outdir, name)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Wrote {path} ({len(data['elements'])} elements)")
