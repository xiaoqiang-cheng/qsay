import json
import time
from pathlib import Path

from dox.evaluation import default_cases_path, evaluate_call, load_cases, percentile, run_evaluation
from dox.providers import Provider


class FakeProvider(Provider):
    name = "fake"

    def complete(self, messages, tools=None, max_tokens=256):
        request = messages[-1]["content"]
        if "extract" in request.lower() or "解压" in request:
            return {
                "tool_calls": [
                    {
                        "function": {
                            "name": "extract_archive",
                            "arguments": json.dumps({"source": "a.tar", "destination": "./out"}),
                        }
                    }
                ]
            }
        return {"content": "NO_CALL"}


class UsageFakeProvider(FakeProvider):
    name = "usage-fake"

    def complete(self, messages, tools=None, max_tokens=256):
        message = super().complete(messages, tools, max_tokens)
        message["_usage"] = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14}
        return message


class SlowFakeProvider(FakeProvider):
    name = "slow-fake"

    def complete(self, messages, tools=None, max_tokens=256):
        time.sleep(0.03)
        return super().complete(messages, tools, max_tokens)


def test_evaluate_call_detects_critical_false_call():
    case = {"class": "negative", "expected_tool": None, "expected_args": {}}
    tool_ok, args_ok, critical = evaluate_call({"name": "remove_path", "arguments": {"path": "build"}}, case)
    assert not tool_ok
    assert critical


def test_evaluate_call_requires_exact_arguments():
    case = {
        "class": "normal",
        "expected_tool": "copy_file",
        "expected_args": {"source": "a", "destination": "b"},
    }
    actual = {
        "name": "copy_file",
        "arguments": {"source": "a", "destination": "b", "overwrite": True},
    }
    tool_ok, args_ok, critical = evaluate_call(actual, case)
    assert tool_ok
    assert not args_ok
    assert not critical


def test_percentile_uses_nearest_rank_for_small_samples():
    assert percentile([1.0, 2.0, 3.0], 0.95) == 3.0


def test_packaged_default_cases_are_available():
    assert len(load_cases(default_cases_path())) == 33


def test_run_evaluation_and_json_report(tmp_path: Path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({"id": "ok", "locale": "en", "request": "extract a.tar into ./out", "expected_tool": "extract_archive", "expected_args": {"source": "a.tar", "destination": "./out"}, "class": "normal"})
        + "\n"
        + json.dumps({"id": "no", "locale": "en", "request": "write a poem", "expected_tool": None, "expected_args": {}, "class": "irrelevant"})
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "report.json"
    result = run_evaluation(FakeProvider(), cases, output)
    assert result["summary"]["tool_exact_rate"] == 1.0
    assert result["summary"]["critical_false_calls"] == 0
    assert json.loads(output.read_text())["summary"]["cases"] == 2


def test_evaluation_report_sums_token_usage(tmp_path: Path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({
            "id": "ok",
            "locale": "en",
            "request": "extract a.tar into ./out",
            "expected_tool": "extract_archive",
            "expected_args": {"source": "a.tar", "destination": "./out"},
            "class": "normal",
        }) + "\n",
        encoding="utf-8",
    )
    result = run_evaluation(UsageFakeProvider(), cases)
    assert result["summary"]["token_usage"] == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
    }
    assert result["rows"][0]["token_usage"]["total_tokens"] == 14


def test_parallel_evaluation_preserves_order_and_reduces_wall_time(tmp_path: Path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text("\n".join(
        json.dumps({
            "id": f"case-{index}",
            "locale": "en",
            "request": "extract a.tar into ./out",
            "expected_tool": "extract_archive",
            "expected_args": {"source": "a.tar", "destination": "./out"},
            "class": "normal",
        })
        for index in range(4)
    ) + "\n", encoding="utf-8")
    result = run_evaluation(SlowFakeProvider(), cases, jobs=4)
    assert [row["id"] for row in result["rows"]] == [f"case-{index}" for index in range(4)]
    assert result["summary"]["jobs"] == 4
    assert result["summary"]["evaluation_ms"] < 100


class ErrorProvider(Provider):
    name = "error"

    def complete(self, messages, tools=None, max_tokens=256):
        raise RuntimeError("request failed")


def test_request_error_is_not_counted_as_correct_no_call(tmp_path: Path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps({
            "id": "irrelevant",
            "locale": "en",
            "request": "write a poem",
            "expected_tool": None,
            "expected_args": {},
            "class": "irrelevant",
        }) + "\n",
        encoding="utf-8",
    )
    result = run_evaluation(ErrorProvider(), cases)
    assert result["summary"]["tool_exact"] == 0
    assert result["summary"]["errors"] == 1
