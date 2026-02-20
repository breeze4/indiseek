#!/usr/bin/env python3
"""Parse Claude Code JSON output and extract eval metrics.

Usage: parse_eval_metrics.py <raw_json_file> <output_file>

Reads the JSON array from --output-format json, extracts the answer text
and appends a metrics section to the bottom of the output markdown file.
"""
import json
import sys
from collections import Counter
from pathlib import Path


def parse_metrics(raw: list[dict]) -> dict:
    """Extract metrics from Claude Code JSON output array."""
    result_obj = None
    tool_uses: list[dict] = []
    assistant_messages = []

    for event in raw:
        etype = event.get("type")

        if etype == "result":
            result_obj = event

        elif etype == "assistant":
            msg = event.get("message", {})
            assistant_messages.append(msg)
            for block in msg.get("content", []):
                if block.get("type") == "tool_use":
                    tool_uses.append(block)

    if not result_obj:
        print("ERROR: No result object found in JSON output", file=sys.stderr)
        sys.exit(1)

    # Count tool calls by name
    tool_call_counts = Counter(t.get("name", "unknown") for t in tool_uses)

    usage = result_obj.get("usage", {})
    model_usage = result_obj.get("modelUsage", {})

    metrics = {
        "duration_ms": result_obj.get("duration_ms"),
        "duration_api_ms": result_obj.get("duration_api_ms"),
        "num_turns": result_obj.get("num_turns"),
        "total_cost_usd": result_obj.get("total_cost_usd"),
        "session_id": result_obj.get("session_id"),
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        },
        "tool_calls": {
            "total": len(tool_uses),
            "by_tool": dict(tool_call_counts.most_common()),
        },
        "model_usage": {
            model: {
                "input_tokens": info.get("inputTokens", 0),
                "output_tokens": info.get("outputTokens", 0),
                "cache_read_input_tokens": info.get("cacheReadInputTokens", 0),
                "cache_creation_input_tokens": info.get("cacheCreationInputTokens", 0),
                "cost_usd": info.get("costUSD", 0),
            }
            for model, info in model_usage.items()
        },
    }

    return metrics


def format_duration(ms: int | None) -> str:
    if not ms:
        return "n/a"
    mins = ms // 60000
    secs = (ms % 60000) // 1000
    return f"{mins}m {secs}s"


def format_metrics_section(metrics: dict) -> str:
    """Format metrics as a markdown section to append to the eval report."""
    dur = metrics["duration_ms"]
    api_dur = metrics["duration_api_ms"]
    cost = metrics["total_cost_usd"] or 0
    turns = metrics["num_turns"] or 0
    tc = metrics["tool_calls"]["total"]
    by_tool = metrics["tool_calls"]["by_tool"]
    inp = metrics["usage"]["input_tokens"]
    out = metrics["usage"]["output_tokens"]
    cache_read = metrics["usage"]["cache_read_input_tokens"]
    cache_create = metrics["usage"]["cache_creation_input_tokens"]

    lines = [
        "",
        "---",
        "",
        "## Eval Metrics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Duration (wall) | {format_duration(dur)} |",
        f"| Duration (API) | {format_duration(api_dur)} |",
        f"| Turns | {turns} |",
        f"| Tool calls | {tc} |",
        f"| Input tokens | {inp:,} |",
        f"| Output tokens | {out:,} |",
        f"| Cache read tokens | {cache_read:,} |",
        f"| Cache creation tokens | {cache_create:,} |",
        f"| Cost | ${cost:.4f} |",
    ]

    if by_tool:
        lines.append("")
        lines.append("### Tool Usage")
        lines.append("")
        lines.append("| Tool | Calls |")
        lines.append("|------|-------|")
        for tool, count in sorted(by_tool.items(), key=lambda x: -x[1]):
            lines.append(f"| {tool} | {count} |")

    if metrics["model_usage"]:
        lines.append("")
        lines.append("### Model Usage")
        lines.append("")
        for model, info in metrics["model_usage"].items():
            lines.append(f"- **{model}**: {info['input_tokens']:,} in / {info['output_tokens']:,} out, ${info['cost_usd']:.4f}")

    lines.append("")
    return "\n".join(lines)


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <raw_json> <output_file>", file=sys.stderr)
        sys.exit(1)

    raw_path, output_path = sys.argv[1], sys.argv[2]

    raw_text = Path(raw_path).read_text()
    raw = json.loads(raw_text)

    result_obj = next((e for e in raw if e.get("type") == "result"), None)
    if not result_obj:
        print("ERROR: No result object in JSON", file=sys.stderr)
        sys.exit(1)

    answer = result_obj.get("result", "")
    metrics = parse_metrics(raw)

    # Write answer + metrics into one file
    content = answer + format_metrics_section(metrics)
    Path(output_path).write_text(content)

    # Print summary to stdout
    dur = metrics["duration_ms"]
    cost = metrics["total_cost_usd"] or 0
    turns = metrics["num_turns"] or 0
    tc = metrics["tool_calls"]["total"]
    inp = metrics["usage"]["input_tokens"]
    out = metrics["usage"]["output_tokens"]

    print(f"  Time:       {format_duration(dur)} (API: {format_duration(metrics['duration_api_ms'])})")
    print(f"  Cost:       ${cost:.4f}")
    print(f"  Turns:      {turns}")
    print(f"  Tool calls: {tc}")
    print(f"  Tokens:     {inp:,} in / {out:,} out")

    if metrics["tool_calls"]["by_tool"]:
        print(f"  Tools:      {', '.join(f'{k}={v}' for k, v in metrics['tool_calls']['by_tool'].items())}")


if __name__ == "__main__":
    main()
