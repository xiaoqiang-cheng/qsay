#!/usr/bin/env python3
"""Evaluate LFM2.5 GGUF with llama.cpp on the shared dox cases."""
from __future__ import annotations

import argparse
import ast
import json
import platform
import re
import statistics
import sys
import time
from pathlib import Path

from llama_cpp import Llama

from needle_eval import TOOLS, evaluate, load_cases


def parse_call(text):
    # LFM2.5 documents Pythonic [name(key="value")] output, but can also emit
    # a JSON list.  Accept both and normalize to the benchmark envelope.
    json_match = re.match(r"\s*\[\s*(\{.*?\})\s*\]", text, re.S)
    if json_match:
        try:
            obj = json.loads(json_match.group(1))
            return {"name": obj["name"], "arguments": obj.get("arguments") or {}}
        except (KeyError, TypeError, json.JSONDecodeError):
            pass
    call = re.match(r"\s*\[([A-Za-z_][A-Za-z0-9_]*)\((.*?)\)\]", text, re.S)
    if not call:
        return None
    try:
        node = ast.parse("f(" + call.group(2) + ")", mode="eval").body
        args = {kw.arg: ast.literal_eval(kw.value) for kw in node.keywords}
    except (SyntaxError, ValueError):
        return None
    return {"name": call.group(1), "arguments": args}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--cases", type=Path, default=Path(__file__).parent.parent / "dox" / "cases.jsonl")
    ap.add_argument("--output", type=Path)
    ap.add_argument("--max-new-tokens", type=int, default=64)
    ap.add_argument("--threads", type=int, default=8)
    args = ap.parse_args()

    start_load = time.perf_counter()
    llm = Llama(model_path=str(args.model), n_ctx=4096, n_threads=args.threads, n_threads_batch=args.threads, n_gpu_layers=0, verbose=False)
    load_ms = (time.perf_counter() - start_load) * 1000
    catalog = json.dumps(TOOLS, ensure_ascii=False, separators=(",", ":"))
    system = ("Route requests in Chinese, English, or mixed language. Return a "
              "Python list with exactly one tool call when all required arguments "
              "are explicit, for example [extract_archive(source=\"a.tar\", "
              "destination=\"./out\")]. Return NO_CALL for unsupported, missing, "
              "negated, or unsafe root/current-directory deletion requests. Do not "
              "explain. Chinese 解压 means extract_archive. List of tools: " + catalog)
    rows = []
    for case in load_cases(args.cases):
        prompt = "<|im_start|>system\n" + system + "<|im_end|>\n<|im_start|>user\n" + case["request"] + "<|im_end|>\n<|im_start|>assistant\n"
        start = time.perf_counter()
        completion = llm.create_completion(prompt, max_tokens=args.max_new_tokens, temperature=0.0, stop=["<|im_end|>"])
        elapsed_ms = (time.perf_counter() - start) * 1000
        output = completion["choices"][0]["text"]
        actual = parse_call(output)
        response = {"function_calls": [actual] if actual else []}
        tool_ok, args_ok, critical, _ = evaluate(response, case)
        rows.append({**case, "elapsed_ms": round(elapsed_ms, 3), "raw_output": output, "actual": actual, "tool_exact": tool_ok, "args_exact": args_ok, "critical_false_call": critical})
        print("{id:24} {ms:7.1f}ms tool={tool} args={arg}".format(id=case["id"], ms=elapsed_ms, tool="ok" if tool_ok else "FAIL", arg="ok" if args_ok else "FAIL"))
        llm.reset()
    normal = [r for r in rows if r["class"] == "normal"]
    times = sorted(r["elapsed_ms"] for r in rows)
    summary = {"model": "lfm2.5-1.2b-instruct", "model_path": str(args.model), "format": "Q4_K_M GGUF", "platform": platform.platform(), "python": sys.version.split()[0], "cases": len(rows), "load_ms": round(load_ms, 3), "tool_exact_rate": sum(r["tool_exact"] for r in rows) / len(rows), "argument_exact_rate": sum(r["args_exact"] for r in normal) / len(normal), "critical_false_calls": sum(r["critical_false_call"] for r in rows), "latency_ms": {"p50": statistics.median(times), "p95": times[max(0, int(len(times) * .95) - 1)]}}
    result = {"summary": summary, "rows": rows}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
