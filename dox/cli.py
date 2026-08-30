from __future__ import annotations

import argparse
import json
import os
import platform as platform_module
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .config import Config, config_command, load_config
from .evaluation import default_cases_path, print_report, run_evaluation
from .providers import APIProvider, Provider, command_system_prompt, download_default_model, local_provider, message_content
from .schema import Plan, PlanError, default_shell, parse_plan


def platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def current_shell(platform: str) -> str:
    if platform == "windows":
        return "powershell"
    return os.environ.get("SHELL", "sh")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dox", description="自然语言命令路由器")
    result.add_argument("request", nargs="*", help="自然语言命令")
    result.add_argument("--print", dest="print_only", action="store_true", help="只打印命令")
    result.add_argument("--copy", action="store_true", help="复制命令，不执行")
    result.add_argument("--json", action="store_true", help="输出结构化计划")
    result.add_argument("--yes", action="store_true", help="跳过普通确认")
    result.add_argument("--explain", action="store_true", help="显示额外计划信息")
    result.add_argument("--lang", choices=["zh", "en"], help="界面语言")
    result.add_argument("--config", type=Path, help="指定配置文件")
    group = result.add_mutually_exclusive_group()
    group.add_argument("--local", action="store_true", help="强制使用本地模型")
    group.add_argument("--api", action="store_true", help="强制使用 API LLM")
    result.add_argument("--offline", action="store_true", help="禁止网络 Provider")
    result.add_argument("--backend", choices=["auto", "llama-cpp", "mlx", "server"], help="本地推理 backend")
    result.add_argument("--model-path", help="临时指定本地模型路径")
    result.add_argument("--version", action="version", version=f"dox {__version__}")
    return result


def provider_for(config: Config, force_local: bool, force_api: bool, offline: bool, backend: Optional[str] = None, model_path: Optional[str] = None) -> Provider:
    if force_api:
        if offline:
            raise RuntimeError("`--api` 与 `--offline` 不能同时使用")
        return APIProvider(config)
    if force_local or offline or config.provider.lower() == "local":
        return local_provider(config, backend, model_path)
    return APIProvider(config)


def plan_request(provider: Provider, request: str, platform: str, language: str) -> Plan:
    shell = current_shell(platform)
    messages = [
        {"role": "system", "content": command_system_prompt(platform, shell, language)},
        {"role": "user", "content": request},
    ]
    message = provider.complete(messages, max_tokens=256)
    return parse_plan(message_content(message), platform, language)


def risk_label(risk: str, language: str) -> str:
    labels = {
        "zh": {"read_only": "只读", "write": "会写入文件", "high": "高风险操作"},
        "en": {"read_only": "read-only", "write": "writes files", "high": "high-risk operation"},
    }
    return labels[language][risk]


def show_plan(plan: Plan, language: str, explain: bool = False) -> None:
    if language == "zh":
        print(f"意图：{plan.intent}")
        print(f"命令：{plan.command}")
        if explain:
            print(f"工具：{', '.join(plan.tools)}")
            print(f"Shell：{plan.shell}")
        print(f"假设：{'；'.join(plan.assumptions)}")
        print(f"风险：{risk_label(plan.risk, language)}")
    else:
        print(f"Intent: {plan.intent}")
        print(f"Command: {plan.command}")
        if explain:
            print(f"Tools: {', '.join(plan.tools)}")
            print(f"Shell: {plan.shell}")
        print(f"Assumptions: {'; '.join(plan.assumptions)}")
        print(f"Risk: {risk_label(plan.risk, language)}")


def copy_command(command: str, platform: str) -> None:
    if platform == "macos":
        program, args = "pbcopy", []
    elif platform == "windows":
        program, args = "clip.exe", []
    else:
        program, args = "xclip", ["-selection", "clipboard"]
    if not shutil.which(program):
        raise RuntimeError(f"剪贴板命令 `{program}` 不可用")
    subprocess.run([program, *args], input=command, text=True, check=True)


def execute_command(plan: Plan, platform: str) -> int:
    if platform == "windows":
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", plan.command]
    else:
        command = [plan.shell, "-lc", plan.command]
    return subprocess.run(command, check=False).returncode


def finish_plan(plan: Plan, args: argparse.Namespace, language: str, platform: str) -> int:
    if plan.clarification:
        prefix = "需要补充信息" if language == "zh" else "More information is needed"
        print(f"{prefix}：{plan.clarification}")
        return 1
    if args.json:
        print(json.dumps(plan.to_dict(), ensure_ascii=False))
        return 0
    if args.print_only:
        print(plan.command)
        return 0
    show_plan(plan, language, args.explain)
    if args.copy:
        try:
            copy_command(plan.command, platform)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            print(f"复制失败：{exc}", file=sys.stderr)
            return 1
        print("命令已复制到剪贴板。" if language == "zh" else "Command copied to clipboard.")
        return 0
    if args.yes:
        if plan.risk == "high":
            print("高风险命令不能使用 `--yes` 自动执行。" if language == "zh" else "High-risk commands cannot use `--yes`.", file=sys.stderr)
            return 2
        return execute_command(plan, platform)
    if not sys.stdin.isatty():
        print("当前不是交互终端；请使用 `--print`、`--copy` 或 `--yes`。" if language == "zh" else "Not an interactive terminal; use `--print`, `--copy`, or `--yes`.", file=sys.stderr)
        return 2
    if language == "zh":
        print("\n执行？\n  y 执行\n  N 取消（默认）\n  e 编辑（暂未实现）\n  c 复制")
        answer = input("选择 [y/N/e/c]：").strip().lower()
    else:
        print("\nExecute?\n  y execute\n  N cancel (default)\n  e edit (not available)\n  c copy")
        answer = input("Choose [y/N/e/c]: ").strip().lower()
    if answer in {"y", "yes"}:
        return execute_command(plan, platform)
    if answer == "c":
        try:
            copy_command(plan.command, platform)
            return 0
        except Exception as exc:
            print(f"复制失败：{exc}", file=sys.stderr)
            return 1
    if answer == "e":
        print("编辑模式暂未实现。" if language == "zh" else "Edit mode is not available yet.")
        return 1
    print("已取消。" if language == "zh" else "Cancelled.")
    return 0


def evaluate_command(argv: Sequence[str]) -> int:
    eval_parser = argparse.ArgumentParser(prog="dox eval", description="评估本地或 API LLM 的工具路由准确率")
    providers = eval_parser.add_mutually_exclusive_group()
    providers.add_argument("--api", action="store_true", help="测试 API LLM")
    providers.add_argument("--local", action="store_true", help="测试本地模型（默认）")
    eval_parser.add_argument("--cases", type=Path, default=default_cases_path())
    eval_parser.add_argument("--output", type=Path, help="保存完整 JSON 报告")
    eval_parser.add_argument("--model", help="临时覆盖 API 模型名")
    eval_parser.add_argument("--base-url", help="临时覆盖 API base URL")
    eval_parser.add_argument("--api-key-env", help="临时覆盖 API Key 环境变量名")
    eval_parser.add_argument("--backend", choices=["auto", "llama-cpp", "mlx", "server"])
    eval_parser.add_argument("--model-path")
    eval_parser.add_argument("--config", type=Path)
    eval_parser.add_argument("--lang", choices=["zh", "en"])
    eval_parser.add_argument("--locale", action="append", choices=["zh", "en", "mixed"], help="只测指定语言，可重复")
    eval_parser.add_argument("--limit", type=int, help="只测前 N 条")
    eval_parser.add_argument("--verbose", action="store_true")
    args = eval_parser.parse_args(argv)
    config = load_config(args.config)
    language = args.lang or config.language
    try:
        if args.api:
            provider = APIProvider(config, args.model, args.base_url, args.api_key_env)
        else:
            provider = local_provider(config, args.backend, args.model_path)
        result = run_evaluation(provider, args.cases, args.output, args.locale, args.limit, args.verbose)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"评估失败：{exc}" if language == "zh" else f"Evaluation failed: {exc}", file=sys.stderr)
        return 2
    print_report(result, language)
    if args.output:
        print(f"\nJSON: {args.output}")
    if result["summary"]["errors"] == result["summary"]["cases"]:
        return 2
    return 0 if result["summary"]["critical_false_calls"] == 0 else 1


def model_command(argv: Sequence[str]) -> int:
    model_parser = argparse.ArgumentParser(prog="dox model")
    sub = model_parser.add_subparsers(dest="action", required=True)
    sub.add_parser("path", help="显示默认模型路径")
    download = sub.add_parser("download", help="下载默认 Qwen3-0.6B GGUF")
    download.add_argument("--output", type=Path)
    download.add_argument("--endpoint", help="Hugging Face endpoint/mirror")
    args = model_parser.parse_args(argv)
    if args.action == "path":
        from .providers import default_model_path
        print(default_model_path())
        return 0
    try:
        path = download_default_model(args.output, args.endpoint or os.environ.get("HF_ENDPOINT"))
    except (RuntimeError, OSError) as exc:
        print(f"模型下载失败：{exc}", file=sys.stderr)
        return 2
    print(path)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "config":
        return config_command(raw[1:])
    if raw and raw[0] == "eval":
        return evaluate_command(raw[1:])
    if raw and raw[0] == "model":
        return model_command(raw[1:])
    args = parser().parse_args(raw)
    if args.copy and args.print_only:
        parser().error("`--copy` 与 `--print` 不能同时使用")
    config = load_config(args.config)
    language = args.lang or config.language
    if not args.request:
        parser().print_help()
        return 2
    request = " ".join(args.request)
    platform = platform_name()
    if platform == "other":
        print("当前平台不受支持。", file=sys.stderr)
        return 2
    try:
        provider = provider_for(config, args.local, args.api, args.offline, args.backend, args.model_path)
        plan = plan_request(provider, request, platform, language)
    except (RuntimeError, PlanError, OSError) as exc:
        prefix = "规划失败" if language == "zh" else "Planning failed"
        print(f"{prefix}：{exc}", file=sys.stderr)
        return 2
    return finish_plan(plan, args, language, platform)


if __name__ == "__main__":
    raise SystemExit(main())
