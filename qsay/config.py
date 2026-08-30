from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional


DEFAULTS = {
    "core.language": "zh",
    "llm.provider": "api",
    "llm.base-url": "https://api.openai.com/v1",
    "llm.model": "",
    "llm.api-key": "",
    "llm.timeout-seconds": "60",
    "local.model": "Qwen3-0.6B",
    "local.backend": "auto",
    "local.model-path": "",
    "local.endpoint": "",
    "local.threads": "0",
    "local.context-size": "2048",
}

SENSITIVE_KEYS = {"llm.api-key"}

ALIASES = {
    "language": "core.language",
    "provider": "llm.provider",
    "base-url": "llm.base-url",
    "api.base-url": "llm.base-url",
    "model": "llm.model",
    "api-key": "llm.api-key",
    "timeout": "llm.timeout-seconds",
    "timeout-seconds": "llm.timeout-seconds",
    "backend": "local.backend",
    "model-path": "local.model-path",
}


def canonical_key(key: str) -> str:
    key = key.strip().lower()
    return ALIASES.get(key, key)


def parse_config(text: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        full_key = f"{section}.{key}" if section else key
        values[canonical_key(full_key)] = value.strip().strip('"\'')
    return values


def read_config(path: Path) -> Dict[str, str]:
    try:
        return parse_config(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def render_config(values: Dict[str, str]) -> str:
    sections: Dict[str, Dict[str, str]] = {}
    for full_key, value in sorted(values.items()):
        section, _, key = full_key.partition(".")
        if not key:
            section, key = "core", section
        sections.setdefault(section, {})[key] = value
    chunks = []
    for section, entries in sorted(sections.items()):
        lines = [f"[{section}]"]
        for key, value in sorted(entries.items()):
            lines.append(f'{key} = "{value.replace(chr(34), chr(92) + chr(34))}"')
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks) + ("\n" if chunks else "")


def system_path() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "qsay" / "config"
    return Path("/etc/qsayconfig")


def global_path() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".qsayconfig"
    return Path.home() / ".qsayconfig"


def local_path() -> Path:
    return Path(".qsay") / "config"


def load_values(explicit: Optional[Path] = None) -> Dict[str, str]:
    values = dict(DEFAULTS)
    if explicit is not None:
        values.update(read_config(explicit))
        return values
    for path in (system_path(), global_path(), local_path()):
        values.update(read_config(path))
    return values


@dataclass(frozen=True)
class Config:
    language: str
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float
    local_model: str
    local_backend: str
    local_model_path: str
    local_endpoint: str
    local_threads: int
    local_context_size: int

    @classmethod
    def from_values(cls, values: Dict[str, str]) -> "Config":
        def integer(key: str, default: int) -> int:
            try:
                return int(values.get(key, str(default)))
            except ValueError:
                return default

        try:
            timeout = float(values.get("llm.timeout-seconds", "60"))
        except ValueError:
            timeout = 60.0
        return cls(
            language=values.get("core.language", "zh"),
            provider=values.get("llm.provider", "api"),
            base_url=values.get("llm.base-url", "https://api.openai.com/v1"),
            model=values.get("llm.model", ""),
            api_key=values.get("llm.api-key", ""),
            timeout_seconds=max(timeout, 1.0),
            local_model=values.get("local.model", "Qwen3-0.6B"),
            local_backend=values.get("local.backend", "auto"),
            local_model_path=values.get("local.model-path", ""),
            local_endpoint=values.get("local.endpoint", ""),
            local_threads=max(integer("local.threads", 0), 0),
            local_context_size=max(integer("local.context-size", 2048), 512),
        )


def load_config(explicit: Optional[Path] = None) -> Config:
    return Config.from_values(load_values(explicit))


def write_config(path: Path, values: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_config(values), encoding="utf-8")
    # API keys are intentionally supported in the config file, so keep user
    # config files private on POSIX systems. Windows ACLs remain authoritative.
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass


def config_command(argv: Iterable[str]) -> int:
    args = list(argv)
    scope = "local"
    scope_explicit = False
    explicit: Optional[Path] = None
    list_values = False
    unset = False
    positional = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg in {"--global", "--local", "--system"}:
            scope = arg[2:]
            scope_explicit = True
        elif arg in {"--list", "-l"}:
            list_values = True
        elif arg == "--unset":
            unset = True
        elif arg == "--file":
            index += 1
            if index >= len(args):
                return _config_error("`--file` 需要一个配置文件路径")
            explicit = Path(args[index])
        elif arg in {"--help", "-h"}:
            print_config_help()
            return 0
        elif arg.startswith("-"):
            return _config_error(f"未知选项 `{arg}`")
        else:
            positional.append(arg)
        index += 1

    paths = {"system": system_path(), "global": global_path(), "local": local_path()}
    path = explicit or paths[scope]
    if list_values:
        values = read_config(path) if scope_explicit or explicit else load_values()
        for key, value in sorted(values.items()):
            shown = "<configured>" if key in SENSITIVE_KEYS and value else value
            print(f"{key}={shown}")
        return 0
    if not positional or len(positional) > 2:
        print_config_help()
        return 2
    key = canonical_key(positional[0])
    if len(positional) == 1 and not unset:
        values = read_config(path) if scope_explicit or explicit else load_values()
        if key not in values:
            return _config_error("配置项不存在")
        print(values[key])
        return 0
    values = read_config(path)
    if unset:
        values.pop(key, None)
    else:
        values[key] = positional[1]
    try:
        write_config(path, values)
    except OSError as exc:
        return _config_error(f"无法写入配置文件 {path}：{exc}")
    return 0


def _config_error(message: str) -> int:
    print(f"qsay config：{message}", file=sys.stderr)
    return 2


def print_config_help() -> None:
    print("""用法：
  qsay config [--global|--local|--system|--file PATH] KEY VALUE
  qsay config [--global|--local|--system|--file PATH] KEY
  qsay config --list
  qsay config --unset KEY

示例：
  qsay config --global llm.provider api
  qsay config --global llm.base-url https://api.openai.com/v1
  qsay config --global llm.model gpt-4o-mini
  qsay config --global llm.api-key YOUR_API_KEY
  qsay config --global llm.provider local
  qsay config --global local.backend auto
  qsay config --global local.model-path /path/to/Qwen3-0.6B-Q4_K_M.gguf
  qsay config --global core.language zh""")
