import pytest

from qsay.schema import PlanError, assess_command_risk, parse_plan, shell_supported


def test_parses_plan_and_escalates_risk():
    plan = parse_plan(
        '{"intent":"delete","command":"rm -rf ./build","shell":"zsh","risk":"write","assumptions":[],"tools":["rm"],"clarification":null}',
        "macos",
        "zh",
    )
    assert plan.risk == "high"
    assert plan.command == "rm -rf ./build"


def test_returns_clarification():
    plan = parse_plan(
        '{"intent":"copy","command":"","shell":"sh","risk":"read_only","assumptions":[],"tools":[],"clarification":"目标目录是什么？"}',
        "linux",
        "zh",
    )
    assert plan.clarification == "目标目录是什么？"


def test_rejects_command_control_characters():
    with pytest.raises(PlanError):
        parse_plan(
            '{"intent":"x","command":"printf hello\\nworld","shell":"sh","risk":"read_only","assumptions":[],"tools":[],"clarification":null}',
            "linux",
            "en",
        )


def test_shell_support():
    assert shell_supported("windows", "powershell")
    assert not shell_supported("windows", "cmd")
    assert shell_supported("linux", "/bin/bash")


def test_risk_classifier():
    assert assess_command_risk("uname -a") == "read_only"
    assert assess_command_risk("mkdir out") == "write"
    assert assess_command_risk("rm -rf /") == "high"
