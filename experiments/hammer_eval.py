#!/usr/bin/env python3
"""Evaluate Hammer2.0-0.5B GGUF with its documented prompt format."""
from __future__ import annotations

import argparse
import json
import platform
import re
import statistics
import sys
import time
from pathlib import Path

from llama_cpp import Llama

from needle_eval import TOOLS, evaluate, load_cases


TASK = """You are a tool calling assistant. Select one appropriate tool and fill its parameters. If no tool applies, the request is negated or unsafe, or a required parameter is missing, output an empty list."""
FORMAT = """Output only JSON: [{"name":"func_name","arguments":{"arg":"value"}}]. If no function call is needed, output []."""


def hammer_tools():
    result = []
    for tool in TOOLS:
        required = set(tool["parameters"].get("required") or [])
        properties = {name: {**value, "required": name in required} for name, value in tool["parameters"]["properties"].items()}
        result.append({"name": tool["name"], "description": tool["description"], "parameters": properties})
    return result


def build_prompt(query):
    return (f"[BEGIN OF TASK INSTRUCTION]\n{TASK}\n[END OF TASK INSTRUCTION]\n\n"
            f"[BEGIN OF AVAILABLE TOOLS]\n{json.dumps(hammer_tools(), separators=(',', ':'))}\n[END OF AVAILABLE TOOLS]\n\n"
            f"[BEGIN OF FORMAT INSTRUCTION]\n{FORMAT}\n[END OF FORMAT INSTRUCTION]\n\n"
            f"[BEGIN OF QUERY]\n{query}\n[END OF QUERY]\n")


def parse_call(text):
    match = re.search(r"\[\s*(\{.*?\})\s*\]", text, re.S)
    if not match:
        return None
    try:
        obj = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
        return None
    return {"name": obj["name"], "arguments": obj.get("arguments") or {}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--cases", type=Path, default=Path(__file__).parent.parent / "dox" / "cases.jsonl")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--max-new-tokens", type=int, default=96)
    args = ap.parse_args()
    start_load = time.perf_counter()
    llm = Llama(model_path=str(args.model), n_ctx=2048, n_threads=args.threads, n_threads_batch=args.threads, n_gpu_layers=0, verbose=False)
    load_ms = (time.perf_counter() - start_load) * 1000
    rows = []
    for case in load_cases(args.cases):
        start = time.perf_counter()
        response = llm.create_chat_completion(messages=[{"role": "user", "content": build_prompt(case["request"])}], temperature=0.0, max_tokens=args.max_new_tokens)
        elapsed_ms = (time.perf_counter() - start) * 1000
        raw = response["choices"][0]["message"].get("content") or ""
        actual = parse_call(raw)
        normalized = {"function_calls": [actual] if actual else []}
        tool_ok, args_ok, critical, _ = evaluate(normalized, case)
        rows.append({**case, "elapsed_ms": round(elapsed_ms, 3), "raw_output": raw, "actual": actual, "tool_exact": tool_ok, "args_exact": args_ok, "critical_false_call": critical})
        print("{id:24} {ms:7.1f}ms tool={tool} args={arg}".format(id=case["id"], ms=elapsed_ms, tool="ok" if tool_ok else "FAIL", arg="ok" if args_ok else "FAIL"))
        llm.reset()
    normal = [r for r in rows if r["class"] == "normal"]
    times = sorted(r["elapsed_ms"] for r in rows)
    summary = {"model": "hammer2.0-0.5b", "model_path": str(args.model), "format": "Q4_K_M GGUF", "backend": "llama.cpp CPU", "license": "CC-BY-4.0", "platform": platform.platform(), "python": sys.version.split()[0], "cases": len(rows), "load_ms": round(load_ms, 3), "tool_exact_rate": sum(r["tool_exact"] for r in rows) / len(rows), "argument_exact_rate": sum(r["args_exact"] for r in normal) / len(normal), "critical_false_calls": sum(r["critical_false_call"] for r in rows), "latency_ms": {"p50": statistics.median(times), "p95": times[max(0, int(len(times) * .95) - 1)]}}
    result = {"summary": summary, "rows": rows}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
