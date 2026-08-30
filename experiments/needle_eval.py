#!/usr/bin/env python3
"""Reproducible Needle 2 routing evaluation for dox.

The model only selects a declared tool and extracts arguments.  This script
never executes a generated command.  Set NEEDLE_TELEMETRY=0 for offline runs.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path


TOOLS = [
    {"name": "extract_archive", "description": "Extract an archive into a destination directory.", "parameters": {"type": "object", "properties": {"source": {"type": "string", "description": "archive path"}, "destination": {"type": "string", "description": "destination directory"}}, "required": ["source", "destination"]}},
    {"name": "copy_file", "description": "Copy one file to a destination path.", "parameters": {"type": "object", "properties": {"source": {"type": "string", "description": "source file path"}, "destination": {"type": "string", "description": "destination file path"}}, "required": ["source", "destination"]}},
    {"name": "list_directory", "description": "List files in a directory.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "directory path; use . for current directory"}}, "required": ["path"]}},
    {"name": "git_status", "description": "Show the Git working tree status for a directory.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Git repository path; use . for current directory"}}, "required": ["path"]}},
    {"name": "find_files", "description": "Find files matching a glob pattern below a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string", "description": "file glob such as *.rs"}, "path": {"type": "string", "description": "directory to search"}}, "required": ["pattern", "path"]}},
    {"name": "remove_path", "description": "Delete a file or directory. This is destructive and always requires explicit confirmation.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "path to delete"}}, "required": ["path"]}},
    {"name": "install_package", "description": "Install a package using a package manager.", "parameters": {"type": "object", "properties": {"manager": {"type": "string", "description": "package manager such as npm or pip"}, "package": {"type": "string", "description": "package name"}}, "required": ["manager", "package"]}},
]


def load_cases(path: Path):
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip() and not line.lstrip().startswith("#")]


def evaluate(response, case):
    calls = response.get("function_calls") or []
    actual = calls[0] if calls else None
    expected_tool = case.get("expected_tool")
    tool_ok = (actual is None and expected_tool is None) or (actual is not None and actual.get("name") == expected_tool)
    expected_args = case.get("expected_args") or {}
    actual_args = (actual or {}).get("arguments") or {}
    args_ok = all(actual_args.get(k) == v for k, v in expected_args.items()) and (expected_tool is None or actual is not None)
    critical = case["class"] in {"negative", "irrelevant", "dangerous"} and expected_tool is None and actual is not None
    return tool_ok, args_ok, critical, actual


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", type=Path, help="Optional .cact LoRA weights; omit for the confidence-calibrated Needle 2 base engine")
    ap.add_argument("--cases", type=Path, default=Path(__file__).parent.parent / "dox" / "cases.jsonl")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--max-new-tokens", type=int, default=96)
    ap.add_argument("--warmup", type=int, default=1)
    args = ap.parse_args()
    try:
        from needle import Needle
    except ImportError as exc:
        raise SystemExit("Install cactus-needle first: python -m pip install cactus-needle") from exc

    cases = load_cases(args.cases)
    agent = Needle(tools=TOOLS, weights=str(args.checkpoint) if args.checkpoint else None)
    for _ in range(args.warmup):
        agent.complete("List files in the current directory", max_new_tokens=args.max_new_tokens)

    rows = []
    for case in cases:
        # Each dox invocation is an independent request. Needle keeps a sliding
        # session internally, so reset it to prevent earlier benchmark text from
        # contaminating tool selection and extracted arguments.
        agent.reset()
        start = time.perf_counter()
        response = agent.complete(case["request"], max_new_tokens=args.max_new_tokens)
        elapsed_ms = (time.perf_counter() - start) * 1000
        tool_ok, args_ok, critical, actual = evaluate(response, case)
        rows.append({**case, "elapsed_ms": round(elapsed_ms, 3), "confidence": response.get("confidence"), "response_type": response.get("type"), "actual": actual, "tool_exact": tool_ok, "args_exact": args_ok, "critical_false_call": critical})
        print("{id:24} {ms:7.1f}ms conf={conf!s:>6} tool={tool} args={arg}".format(id=case["id"], ms=elapsed_ms, conf=response.get("confidence"), tool="ok" if tool_ok else "FAIL", arg="ok" if args_ok else "FAIL"))

    normal = [r for r in rows if r["class"] == "normal"]
    summary = {
        "model": "needle2",
        "checkpoint": str(args.checkpoint) if args.checkpoint else None,
        "engine": getattr(__import__("needle"), "__version__", "unknown"),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cases": len(rows),
        "tool_exact_rate": sum(r["tool_exact"] for r in rows) / len(rows),
        "argument_exact_rate": sum(r["args_exact"] for r in normal) / len(normal),
        "critical_false_calls": sum(r["critical_false_call"] for r in rows),
        "latency_ms": {"p50": statistics.median(r["elapsed_ms"] for r in rows), "p95": sorted(r["elapsed_ms"] for r in rows)[max(0, int(len(rows) * .95) - 1)], "cold_start_excluded": args.warmup > 0},
    }
    result = {"summary": summary, "rows": rows}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
