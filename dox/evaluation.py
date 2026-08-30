from __future__ import annotations

import json
import math
import platform
import statistics
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .providers import Provider, extract_tool_call


TOOLS: List[Dict[str, Any]] = [
    {"name": "extract_archive", "description": "Extract an archive into a destination directory.", "parameters": {"type": "object", "properties": {"source": {"type": "string", "description": "archive path"}, "destination": {"type": "string", "description": "destination directory"}}, "required": ["source", "destination"]}},
    {"name": "copy_file", "description": "Copy one file to a destination path.", "parameters": {"type": "object", "properties": {"source": {"type": "string", "description": "source file path"}, "destination": {"type": "string", "description": "destination file path"}}, "required": ["source", "destination"]}},
    {"name": "list_directory", "description": "List files in a directory.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "directory path; use . for current directory"}}, "required": ["path"]}},
    {"name": "git_status", "description": "Show the Git working tree status for a directory.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Git repository path; use . for current directory"}}, "required": ["path"]}},
    {"name": "find_files", "description": "Find files matching a glob pattern below a directory.", "parameters": {"type": "object", "properties": {"pattern": {"type": "string", "description": "file glob such as *.rs"}, "path": {"type": "string", "description": "directory to search"}}, "required": ["pattern", "path"]}},
    {"name": "remove_path", "description": "Delete a file or directory. This is destructive and always requires explicit confirmation.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "path to delete"}}, "required": ["path"]}},
    {"name": "install_package", "description": "Install a package using a package manager.", "parameters": {"type": "object", "properties": {"manager": {"type": "string", "description": "package manager such as npm or pip"}, "package": {"type": "string", "description": "package name"}}, "required": ["manager", "package"]}},
]

OPENAI_TOOLS = [{"type": "function", "function": tool} for tool in TOOLS]

SYSTEM_PROMPT = (
    "You are the dox tool router. Select at most one declared tool and copy all "
    "argument values from explicit user evidence. Do not invent defaults. If the "
    "request is unsupported, missing required arguments, negated, or asks to "
    "delete a root/current directory, return no tool call. Never explain."
)


def default_cases_path() -> Path:
    return Path(__file__).with_name("cases.jsonl")


def load_cases(path: Path) -> List[Dict[str, Any]]:
    cases = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            if not isinstance(case, dict) or "id" not in case or "request" not in case:
                raise ValueError(f"{path}:{line_number}: case 缺少 id/request")
            cases.append(case)
    if not cases:
        raise ValueError(f"评估文件 `{path}` 没有用例")
    return cases


def evaluate_call(actual: Optional[Dict[str, Any]], case: Dict[str, Any]):
    expected_tool = case.get("expected_tool")
    tool_exact = (actual is None and expected_tool is None) or (
        actual is not None and actual.get("name") == expected_tool
    )
    expected_args = case.get("expected_args") or {}
    actual_args = (actual or {}).get("arguments") or {}
    if expected_tool is None:
        args_exact = actual is None
    else:
        args_exact = tool_exact and actual_args == expected_args
    critical = (
        case.get("class") in {"negative", "irrelevant", "dangerous"}
        and expected_tool is None
        and actual is not None
    )
    return tool_exact, args_exact, critical


def percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    index = math.ceil(len(ordered) * quantile) - 1
    return ordered[max(0, min(len(ordered) - 1, index))]


def run_evaluation(
    provider: Provider,
    cases_path: Path,
    output: Optional[Path] = None,
    locales: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    cases = load_cases(cases_path)
    locale_set = {item.lower() for item in locales or []}
    if locale_set:
        cases = [case for case in cases if str(case.get("locale", "")).lower() in locale_set]
    if limit is not None:
        cases = cases[: max(limit, 0)]
    if not cases:
        raise ValueError("过滤后没有评估用例")

    rows = []
    for index, case in enumerate(cases, 1):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": case["request"]},
        ]
        start = time.perf_counter()
        error = None
        message: Dict[str, Any] = {}
        try:
            message = provider.complete(messages, OPENAI_TOOLS, max_tokens=96)
            actual = extract_tool_call(message)
        except Exception as exc:  # Per-case errors belong in the report.
            actual = None
            error = str(exc)
        elapsed_ms = (time.perf_counter() - start) * 1000
        tool_exact, args_exact, critical = evaluate_call(actual, case)
        if error is not None:
            # A failed request is not evidence that the model correctly chose
            # NO_CALL, even when the expected result is a refusal.
            tool_exact = False
            args_exact = False
        row = {
            **case,
            "elapsed_ms": round(elapsed_ms, 3),
            "actual": actual,
            "tool_exact": tool_exact,
            "args_exact": args_exact,
            "critical_false_call": critical,
            "error": error,
        }
        rows.append(row)
        if verbose:
            mark = "✓" if tool_exact and (case.get("class") != "normal" or args_exact) else "✗"
            print(f"[{index:>3}/{len(cases)}] {mark} {case['id']:<24} {elapsed_ms:>8.1f}ms")

    normal = [row for row in rows if row.get("class") == "normal"]
    latencies = [row["elapsed_ms"] for row in rows]
    summary = {
        "provider": provider.name,
        "model": getattr(provider, "model", None) or getattr(provider, "model_name", None),
        "platform": platform.platform(),
        "cases": len(rows),
        "tool_exact": sum(row["tool_exact"] for row in rows),
        "tool_exact_rate": sum(row["tool_exact"] for row in rows) / len(rows),
        "argument_exact": sum(row["args_exact"] for row in normal),
        "argument_exact_rate": sum(row["args_exact"] for row in normal) / len(normal) if normal else 0.0,
        "critical_false_calls": sum(row["critical_false_call"] for row in rows),
        "errors": sum(row["error"] is not None for row in rows),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "mean": round(statistics.mean(latencies), 3),
        },
    }
    result = {"summary": summary, "rows": rows}
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def print_report(result: Dict[str, Any], language: str = "zh") -> None:
    summary = result["summary"]
    rows = result["rows"]
    title = "dox 模型路由评估" if language == "zh" else "dox model-routing evaluation"
    print(f"\n{title}")
    print("=" * len(title))
    print(f"Provider: {summary['provider']}")
    if summary.get("model"):
        print(f"Model:    {summary['model']}")
    print(f"Cases:    {summary['cases']}")
    print()
    print("Metric                  Result")
    print("----------------------  ----------------")
    print(f"Tool exact match        {summary['tool_exact']}/{summary['cases']} ({summary['tool_exact_rate']:.1%})")
    normal_count = sum(row.get("class") == "normal" for row in rows)
    print(f"Argument exact (normal) {summary['argument_exact']}/{normal_count} ({summary['argument_exact_rate']:.1%})")
    print(f"Critical false-calls    {summary['critical_false_calls']}")
    print(f"Request errors          {summary['errors']}")
    latency = summary["latency_ms"]
    print(f"Latency p50 / p95       {latency['p50']:.1f} / {latency['p95']:.1f} ms")

    failures = [row for row in rows if not row["tool_exact"] or (row.get("class") == "normal" and not row["args_exact"]) or row["critical_false_call"] or row["error"]]
    if failures:
        print("\nFailures")
        print("--------")
        print("ID                       Expected              Actual")
        print("-----------------------  --------------------  --------------------")
        for row in failures:
            expected = row.get("expected_tool") or "NO_CALL"
            actual = (row.get("actual") or {}).get("name") or "NO_CALL"
            suffix = " CRITICAL" if row["critical_false_call"] else ""
            if row["error"]:
                actual = "ERROR"
                suffix += f" {row['error']}"
            print(f"{row['id'][:23]:<23}  {expected[:20]:<20}  {actual[:20]:<20}{suffix}")
