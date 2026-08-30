import json
from pathlib import Path

from dox.evaluation import OPENAI_TOOLS, evaluate_call, print_report, run_evaluation
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


def test_evaluate_call_detects_critical_false_call():
    case = {"class": "negative", "expected_tool": None, "expected_args": {}}
    tool_ok, args_ok, critical = evaluate_call({"name": "remove_path", "arguments": {"path": "build"}}, case)
    assert not tool_ok
    assert critical


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
