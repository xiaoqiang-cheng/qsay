from __future__ import annotations

import json
import math
import os
import platform
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .providers import Provider, extract_tool_call, message_content, message_timing, message_usage, task_system_prompt
from .schema import PlanError, parse_response


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
    "你是 dox 工具路由器。最多选择一个工具。只有用户明确提供了工具 schema "
    "中每一个 required 参数时才可调用；缺少任意参数必须不调用。禁止把未提及的 "
    "destination 推断为 .、当前目录或源文件所在目录。English: Call a tool only "
    "if every required argument is explicitly present. Never default a missing "
    "destination to . or the current/source directory. Return no tool call for "
    "unsupported, negated, irrelevant, or root/current-directory deletion requests. "
    "参数值必须逐字复制，不能删除、翻译或改写路径的任何部分，包括中文“我的”。"
    "Copy argument values verbatim; never drop, translate, or rewrite a path segment. "
    "Do not reason or explain; respond immediately."
)


def default_cases_path() -> Path:
    return Path(__file__).with_name("cases.jsonl")


def _runtime_platform() -> str:
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def _runtime_shell(platform_name: str) -> str:
    if platform_name == "windows":
        return "powershell"
    return os.environ.get("SHELL", "sh")


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


def _evaluate_case(provider: Provider, case: Dict[str, Any]) -> Dict[str, Any]:
    """Run one case; kept separate so API cases can run concurrently."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": case["request"]},
    ]
    start = time.perf_counter()
    error = None
    message: Dict[str, Any] = {}
    try:
        # Tool calls only need a short function name and argument object. A
        # smaller cap prevents a model from spending time on explanations.
        message = provider.complete(messages, OPENAI_TOOLS, max_tokens=64)
        actual = extract_tool_call(message)
        usage = message_usage(message)
        provider_timing = message_timing(message)
    except Exception as exc:  # Per-case errors belong in the report.
        actual = None
        usage = None
        provider_timing = None
        error = str(exc)
    elapsed_ms = (time.perf_counter() - start) * 1000
    tool_exact, args_exact, critical = evaluate_call(actual, case)
    if error is not None:
        # A failed request is not evidence that the model correctly chose
        # NO_CALL, even when the expected result is a refusal.
        tool_exact = False
        args_exact = False
    return {
        **case,
        "elapsed_ms": round(elapsed_ms, 3),
        "actual": actual,
        "token_usage": usage,
        "timing_ms": {
            "request_ms": round(elapsed_ms, 3),
            **(provider_timing or {}),
        },
        "tool_exact": tool_exact,
        "args_exact": args_exact,
        "critical_false_call": critical,
        "error": error,
    }


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
    wall_start = time.perf_counter()
    # Evaluation intentionally stays serial: concurrent requests can introduce
    # provider queueing/rate-limit noise and obscure single-request latency.
    rows = [_evaluate_case(provider, case) for case in cases]

    if verbose:
        for index, row in enumerate(rows, 1):
            mark = "✓" if row["tool_exact"] and (row.get("class") != "normal" or row["args_exact"]) else "✗"
            print(f"[{index:>3}/{len(rows)}] {mark} {row['id']:<24} {row['elapsed_ms']:>8.1f}ms")

    normal = [row for row in rows if row.get("class") == "normal"]
    latencies = [row["elapsed_ms"] for row in rows]
    api_roundtrips = [
        row["timing_ms"]["api_roundtrip_ms"]
        for row in rows
        if isinstance(row.get("timing_ms", {}).get("api_roundtrip_ms"), (int, float))
    ]
    token_totals = {
        key: sum((row.get("token_usage") or {}).get(key) or 0 for row in rows)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    if any("reasoning_tokens" in (row.get("token_usage") or {}) for row in rows):
        token_totals["reasoning_tokens"] = sum(
            (row.get("token_usage") or {}).get("reasoning_tokens") or 0 for row in rows
        )
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
        "token_usage": token_totals,
        "evaluation_ms": round((time.perf_counter() - wall_start) * 1000, 3),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "mean": round(statistics.mean(latencies), 3),
        },
    }
    if api_roundtrips:
        summary["api_roundtrip_ms"] = {
            "p50": round(statistics.median(api_roundtrips), 3),
            "p95": round(percentile(api_roundtrips, 0.95), 3),
            "mean": round(statistics.mean(api_roundtrips), 3),
        }
    result = {"summary": summary, "rows": rows}
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().casefold()


def run_task_evaluation(
    provider: Provider,
    cases_path: Path,
    task: str,
    output: Optional[Path] = None,
    locales: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
    verbose: bool = False,
    language: str = "zh",
    target_language: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate structured translation or answer responses.

    Cases use ``expected_text`` for exact normalized matching, or
    ``expected_contains`` for answers where several phrasings are acceptable.
    """
    if task not in {"translate", "answer"}:
        raise ValueError("文本评估任务必须是 translate 或 answer")
    cases = load_cases(cases_path)
    locale_set = {item.lower() for item in locales or []}
    if locale_set:
        cases = [case for case in cases if str(case.get("locale", "")).lower() in locale_set]
    if limit is not None:
        cases = cases[: max(limit, 0)]
    if not cases:
        raise ValueError("过滤后没有评估用例")

    platform_name = _runtime_platform()
    shell = _runtime_shell(platform_name)
    rows: List[Dict[str, Any]] = []
    wall_start = time.perf_counter()
    for case in cases:
        messages = [
            {"role": "system", "content": task_system_prompt(platform_name, shell, language, task, target_language)},
            {"role": "user", "content": case["request"]},
        ]
        started = time.perf_counter()
        error: Optional[str] = None
        actual_type: Optional[str] = None
        actual_text: Optional[str] = None
        usage = None
        provider_timing = None
        try:
            message = provider.complete(messages, max_tokens=256)
            plan = parse_response(message_content(message), platform_name, language, expected_type=task)
            actual_type = plan.type
            actual_text = plan.text
            usage = message_usage(message)
            provider_timing = message_timing(message)
        except (Exception,) as exc:  # Per-case errors belong in the report.
            error = str(exc)
        elapsed_ms = (time.perf_counter() - started) * 1000
        expected_text = case.get("expected_text")
        expected_contains = case.get("expected_contains")
        type_exact = actual_type == task
        if isinstance(expected_text, str):
            text_exact = type_exact and _normalize_text(actual_text) == _normalize_text(expected_text)
        elif isinstance(expected_contains, str):
            text_exact = type_exact and _normalize_text(expected_contains) in _normalize_text(actual_text)
        elif isinstance(expected_contains, list):
            text_exact = type_exact and all(
                _normalize_text(item) in _normalize_text(actual_text) for item in expected_contains
            )
        else:
            text_exact = type_exact and bool(_normalize_text(actual_text))
        if error:
            type_exact = False
            text_exact = False
        rows.append({
            **case,
            "elapsed_ms": round(elapsed_ms, 3),
            "actual": {"type": actual_type, "text": actual_text},
            "token_usage": usage,
            "timing_ms": {"request_ms": round(elapsed_ms, 3), **(provider_timing or {})},
            "type_exact": type_exact,
            "text_exact": text_exact,
            "error": error,
        })

    latencies = [row["elapsed_ms"] for row in rows]
    token_totals = {
        key: sum((row.get("token_usage") or {}).get(key) or 0 for row in rows)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    summary: Dict[str, Any] = {
        "provider": provider.name,
        "model": getattr(provider, "model", None) or getattr(provider, "model_name", None),
        "task": task,
        "platform": platform.platform(),
        "cases": len(rows),
        "type_exact": sum(row["type_exact"] for row in rows),
        "type_exact_rate": sum(row["type_exact"] for row in rows) / len(rows),
        "text_exact": sum(row["text_exact"] for row in rows),
        "text_exact_rate": sum(row["text_exact"] for row in rows) / len(rows),
        "errors": sum(row["error"] is not None for row in rows),
        "token_usage": token_totals,
        "evaluation_ms": round((time.perf_counter() - wall_start) * 1000, 3),
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
    if verbose:
        for index, row in enumerate(rows, 1):
            mark = "✓" if row["text_exact"] else "✗"
            print(f"[{index:>3}/{len(rows)}] {mark} {row['id']:<24} {row['elapsed_ms']:>8.1f}ms")
    return result


def print_task_report(result: Dict[str, Any], language: str = "zh") -> None:
    summary = result["summary"]
    rows = result["rows"]
    title = "dox 文本任务评估" if language == "zh" else "dox text-task evaluation"
    print(f"\n{title}")
    print("=" * len(title))
    print(f"Provider: {summary['provider']}")
    if summary.get("model"):
        print(f"Model:    {summary['model']}")
    print(f"Task:     {summary['task']}")
    print(f"Cases:    {summary['cases']}")
    print()
    print("Metric                  Result")
    print("----------------------  ----------------")
    print(f"Type exact match        {summary['type_exact']}/{summary['cases']} ({summary['type_exact_rate']:.1%})")
    print(f"Text match              {summary['text_exact']}/{summary['cases']} ({summary['text_exact_rate']:.1%})")
    print(f"Request errors          {summary['errors']}")
    tokens = summary.get("token_usage") or {}
    print(f"Tokens input / output   {tokens.get('input_tokens', 0)} / {tokens.get('output_tokens', 0)}")
    latency = summary["latency_ms"]
    print(f"Latency p50 / p95       {latency['p50']:.1f} / {latency['p95']:.1f} ms")

    failures = [row for row in rows if not row["text_exact"] or row["error"]]
    if failures:
        print("\nFailures")
        print("--------")
        for row in failures:
            actual = row.get("actual") or {}
            detail = row.get("error") or actual.get("text") or "NO_TEXT"
            print(f"{row['id'][:23]:<23}  {detail[:60]}")


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
    tokens = summary.get("token_usage") or {}
    print(f"Tokens input / output   {tokens.get('input_tokens', 0)} / {tokens.get('output_tokens', 0)}")
    if tokens.get("reasoning_tokens"):
        print(f"Reasoning tokens        {tokens['reasoning_tokens']}")
    latency = summary["latency_ms"]
    print(f"Latency p50 / p95       {latency['p50']:.1f} / {latency['p95']:.1f} ms")
    if summary.get("api_roundtrip_ms"):
        api_latency = summary["api_roundtrip_ms"]
        print(f"API roundtrip p50/p95   {api_latency['p50']:.1f} / {api_latency['p95']:.1f} ms")
    print(f"Evaluation wall time    {summary['evaluation_ms']:.1f} ms")

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
