import json

from dox.cli import main, plan_request, show_plan
from dox.providers import Provider


class UsageProvider(Provider):
    name = "usage-test"

    def complete(self, messages, tools=None, max_tokens=256):
        return {
            "content": json.dumps({
                "intent": "git.status",
                "command": "git status",
                "shell": "sh",
                "risk": "read_only",
                "assumptions": [],
                "tools": ["git"],
                "clarification": None,
            }),
            "_usage": {
                "input_tokens": 41,
                "output_tokens": 9,
                "total_tokens": 50,
            },
        }


def test_default_cli_explains_api_configuration(tmp_path, capsys):
    missing_config = tmp_path / "missing-config"
    exit_code = main([
        "--config",
        str(missing_config),
        "--print",
        "查看 git 状态",
    ])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "尚未配置 API" in captured.err
    assert "dox config --global llm.model" in captured.err


def test_plan_displays_provider_token_usage(capsys):
    plan = plan_request(UsageProvider(), "查看 git 状态", "macos", "zh")
    show_plan(plan, "zh")
    output = capsys.readouterr().out
    assert "Token：输入 41，输出 9，总计 50" in output


def test_json_plan_includes_token_usage(capsys):
    plan = plan_request(UsageProvider(), "查看 git 状态", "macos", "zh")
    assert plan.to_dict()["token_usage"]["total_tokens"] == 50
