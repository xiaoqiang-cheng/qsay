from __future__ import annotations

import json
import os
import platform as platform_module
import re
import sys
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .config import Config


DEFAULT_LOCAL_MODEL = "Qwen3-0.6B"
DEFAULT_GGUF_NAME = "Qwen3-0.6B-Q4_K_M.gguf"
DEFAULT_GGUF_REPO = "unsloth/Qwen3-0.6B-GGUF"


PLAN_SCHEMA = (
    '{"intent":string,"command":string,"shell":string,'
    '"risk":"read_only"|"write"|"high","assumptions":[string],'
    '"tools":[string],"clarification":string|null}'
)


def command_system_prompt(platform: str, shell: str, language: str) -> str:
    language_name = "Chinese" if language == "zh" else "English"
    return (
        "You are dox, a natural-language command router. Return exactly one JSON "
        "object and no markdown. Do not use a reasoning mode, provide chain-of-thought, "
        "or write long explanations; output the final JSON immediately. "
        "Understand the user's request and propose one command for the current "
        "environment. If the request is ambiguous or unsafe to complete reliably, "
        "set clarification to a concise question and command to an empty string. "
        f"JSON schema: {PLAN_SCHEMA}. The command must target platform={platform}, "
        f"shell={shell}, user interface language={language_name}. Never include "
        "secrets or pretend to have run the command."
    )


def chat_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def post_chat(
    base_url: str,
    model: str,
    messages: List[Dict[str, str]],
    timeout: float,
    api_key: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.0,
    max_tokens: int = 256,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    else:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        chat_endpoint(base_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body or exc.reason}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("API 响应中缺少 choices[0].message") from exc
    if not isinstance(message, dict):
        raise RuntimeError("API message 不是 JSON object")
    message = dict(message)
    usage = normalize_usage(data.get("usage"))
    if usage:
        message["_usage"] = usage
    return message


def normalize_usage(value: Any, estimated: bool = False) -> Optional[Dict[str, Any]]:
    """Normalize common OpenAI/local usage fields for a stable CLI report."""
    if not isinstance(value, dict):
        return None

    def integer(*keys: str) -> Optional[int]:
        for key in keys:
            item = value.get(key)
            if isinstance(item, bool):
                continue
            if isinstance(item, int):
                return item
            if isinstance(item, float) and item.is_integer():
                return int(item)
        return None

    input_tokens = integer("input_tokens", "prompt_tokens")
    output_tokens = integer("output_tokens", "completion_tokens")
    total_tokens = integer("total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    if input_tokens is None and output_tokens is None and total_tokens is None:
        return None
    result: Dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    if estimated:
        result["estimated"] = True
    return result


def message_usage(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    value = message.get("_usage")
    return value if isinstance(value, dict) else None


class Provider(ABC):
    name: str

    @abstractmethod
    def complete(self, messages: List[Dict[str, str]], tools: Optional[List[Dict[str, Any]]] = None, max_tokens: int = 256) -> Dict[str, Any]:
        raise NotImplementedError


class APIProvider(Provider):
    name = "api"

    def __init__(self, config: Config, model: Optional[str] = None, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.model = model or config.model
        self.base_url = base_url or config.base_url
        self.api_key = config.api_key if api_key is None else api_key
        self.timeout = config.timeout_seconds
        if not self.model:
            raise RuntimeError(
                "尚未配置 API。请运行：\n"
                "  dox config --global llm.base-url https://api.openai.com/v1\n"
                "  dox config --global llm.model YOUR_MODEL\n"
                "  dox config --global llm.api-key YOUR_API_KEY\n"
                "也可以使用 `dox --local ...` "
                "切换到本地模型。"
            )
        if not self.api_key:
            raise RuntimeError(
                "尚未配置 API Key。请运行：\n"
                "  dox config --global llm.api-key YOUR_API_KEY\n"
                "API Key 将保存在用户配置文件中；也可以使用 `dox --local ...` "
                "切换到本地模型。"
            )

    def complete(self, messages: List[Dict[str, str]], tools: Optional[List[Dict[str, Any]]] = None, max_tokens: int = 256) -> Dict[str, Any]:
        return post_chat(self.base_url, self.model, messages, self.timeout, self.api_key, tools, 0.0, max_tokens)


def model_cache_dir() -> Path:
    override = os.environ.get("DOX_MODEL_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return root / "dox" / "models"
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "dox" / "models"


def default_model_path() -> Path:
    return model_cache_dir() / "qwen3-0.6b" / DEFAULT_GGUF_NAME


def resolve_local_backend(config: Config, override: Optional[str] = None) -> str:
    backend = (override or config.local_backend or "auto").lower()
    if backend != "auto":
        return backend
    if config.local_endpoint:
        return "server"
    # llama.cpp/GGUF is the common default across macOS, Linux and Windows.
    # MLX remains an explicit Apple-Silicon accelerator until its output path is
    # fully equivalent in the product provider.
    return "llama-cpp"


@contextmanager
def suppress_native_output() -> Iterator[None]:
    """Silence native-library startup logs while preserving Python exceptions."""
    if os.environ.get("DOX_LLAMA_LOG"):
        yield
        return

    saved_descriptors = []
    null_descriptor = os.open(os.devnull, os.O_WRONLY)
    try:
        for stream, descriptor in ((sys.stdout, 1), (sys.stderr, 2)):
            try:
                stream.flush()
                saved = os.dup(descriptor)
                os.dup2(null_descriptor, descriptor)
            except (AttributeError, OSError):
                continue
            saved_descriptors.append((descriptor, saved))
        yield
    finally:
        for descriptor, saved in reversed(saved_descriptors):
            os.dup2(saved, descriptor)
            os.close(saved)
        os.close(null_descriptor)


class LlamaCppProvider(Provider):
    name = "local:llama-cpp"

    def __init__(self, config: Config, model_path: Optional[str] = None):
        try:
            # llama.cpp may probe every compiled Metal kernel during import and
            # print harmless "not supported" lines even with verbose=False.
            with suppress_native_output():
                from llama_cpp import Llama
        except ImportError as exc:
            raise RuntimeError(
                "缺少本地推理依赖。请运行 `python -m pip install 'dox-cli[local]'`"
            ) from exc
        path = Path(model_path or config.local_model_path or default_model_path())
        if not path.is_file():
            raise RuntimeError(
                f"未找到本地模型 `{path}`。运行 `dox model download`，"
                "或配置 `local.model-path`。"
            )
        threads = config.local_threads or max(1, (os.cpu_count() or 2) // 2)
        self.model_path = path
        self.model_name = config.local_model or DEFAULT_LOCAL_MODEL
        with suppress_native_output():
            self._llm = Llama(
                model_path=str(path),
                n_ctx=config.local_context_size,
                n_threads=threads,
                n_threads_batch=threads,
                n_gpu_layers=0,
                verbose=False,
            )

    def complete(self, messages: List[Dict[str, str]], tools: Optional[List[Dict[str, Any]]] = None, max_tokens: int = 256) -> Dict[str, Any]:
        local_messages = [dict(item) for item in messages]
        if local_messages and local_messages[-1].get("role") == "user":
            local_messages[-1]["content"] = local_messages[-1].get("content", "") + "\n/no_think"
        response = self._llm.create_chat_completion(
            messages=local_messages,
            tools=tools,
            temperature=0.0,
            max_tokens=max_tokens,
            seed=0,
        )
        self._llm.reset()
        message = dict(response["choices"][0]["message"])
        usage = normalize_usage(response.get("usage"))
        if usage:
            message["_usage"] = usage
        return message


class ServerLocalProvider(Provider):
    name = "local:server"

    def __init__(self, config: Config):
        if not config.local_endpoint:
            raise RuntimeError("`local.endpoint` 未配置")
        self.base_url = config.local_endpoint
        self.model = config.local_model or DEFAULT_LOCAL_MODEL
        self.timeout = config.timeout_seconds

    def complete(self, messages: List[Dict[str, str]], tools: Optional[List[Dict[str, Any]]] = None, max_tokens: int = 256) -> Dict[str, Any]:
        local_messages = [dict(item) for item in messages]
        if local_messages and local_messages[-1].get("role") == "user":
            local_messages[-1]["content"] = local_messages[-1].get("content", "") + "\n/no_think"
        return post_chat(self.base_url, self.model, local_messages, self.timeout, None, tools, 0.0, max_tokens)


class MLXProvider(Provider):
    name = "local:mlx"

    def __init__(self, config: Config, model_path: Optional[str] = None):
        if sys.platform != "darwin" or platform_module.machine() != "arm64":
            raise RuntimeError("MLX backend 仅支持 Apple Silicon macOS")
        try:
            from mlx_lm import generate, load
            from mlx_lm.sample_utils import make_sampler
        except ImportError as exc:
            raise RuntimeError("缺少 MLX；请运行 `python -m pip install mlx-lm`") from exc
        path = model_path or config.local_model_path
        if not path:
            raise RuntimeError("MLX backend 需要配置 `local.model-path`")
        self.model_name = config.local_model or DEFAULT_LOCAL_MODEL
        self._generate = generate
        self._sampler = make_sampler(0.0)
        self._model, self._tokenizer = load(path)

    def complete(self, messages: List[Dict[str, str]], tools: Optional[List[Dict[str, Any]]] = None, max_tokens: int = 256) -> Dict[str, Any]:
        template_tools = [item.get("function", item) for item in (tools or [])]
        prompt = self._tokenizer.apply_chat_template(
            messages,
            tools=template_tools or None,
            add_generation_prompt=True,
            tokenize=False,
            enable_thinking=False,
        )
        content = self._generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            sampler=self._sampler,
            verbose=False,
        )
        message: Dict[str, Any] = {"role": "assistant", "content": content}
        try:
            usage = normalize_usage(
                {
                    "input_tokens": len(self._tokenizer.encode(prompt)),
                    "output_tokens": len(self._tokenizer.encode(content)),
                },
                estimated=True,
            )
        except (AttributeError, TypeError, ValueError):
            usage = None
        if usage:
            message["_usage"] = usage
        return message


def local_provider(config: Config, backend: Optional[str] = None, model_path: Optional[str] = None) -> Provider:
    selected = resolve_local_backend(config, backend)
    if selected in {"llama", "llama.cpp", "llama-cpp", "gguf"}:
        return LlamaCppProvider(config, model_path)
    if selected in {"server", "endpoint", "openai-compatible"}:
        return ServerLocalProvider(config)
    if selected == "mlx":
        return MLXProvider(config, model_path)
    raise RuntimeError(f"未知本地 backend `{selected}`")


def message_content(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    return ""


def extract_tool_call(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    calls = message.get("tool_calls")
    if isinstance(calls, list) and calls:
        call = calls[0]
        function = call.get("function", {}) if isinstance(call, dict) else {}
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return None
        if isinstance(function.get("name"), str) and isinstance(arguments, dict):
            return {"name": function["name"], "arguments": arguments}
    content = message_content(message)
    match = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, re.S)
    if match:
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
        if isinstance(value, dict) and isinstance(value.get("name"), str):
            return {"name": value["name"], "arguments": value.get("arguments") or {}}
    # Some compatible APIs return a bare JSON tool call despite the template.
    stripped = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(value, dict) and isinstance(value.get("name"), str):
        return {"name": value["name"], "arguments": value.get("arguments") or {}}
    return None


def download_default_model(destination: Optional[Path] = None, endpoint: Optional[str] = None) -> Path:
    # Prefer Q4_K_M for the default footprint. The current Qwen organization
    # publishes only Q8_0 GGUF, so this downloads a community conversion for
    # development. Release artifacts must be converted from official weights
    # and pinned by checksum; users can always supply that path explicitly.
    target = destination or default_model_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("下载模型需要 `huggingface-hub`：python -m pip install huggingface-hub") from exc
    downloaded = hf_hub_download(
        repo_id=DEFAULT_GGUF_REPO,
        filename=DEFAULT_GGUF_NAME,
        local_dir=str(target.parent),
        endpoint=endpoint,
    )
    path = Path(downloaded)
    if path != target:
        path.replace(target)
    return target
