import io
import json
import os
import urllib.error
from pathlib import Path

import pytest

from qsay.providers import (
    APIProvider,
    chat_endpoint,
    extract_tool_call,
    is_deepseek_v4_model,
    is_qwen_model,
    message_content,
    normalize_usage,
    post_chat,
    resolve_local_backend,
    suppress_native_output,
    thinking_parameters,
)
from qsay.config import Config, load_values


def test_chat_endpoint():
    assert chat_endpoint("https://example.test/v1") == "https://example.test/v1/chat/completions"
    full = "https://example.test/v1/chat/completions"
    assert chat_endpoint(full) == full


def test_normalizes_openai_token_usage():
    assert normalize_usage({"prompt_tokens": 21, "completion_tokens": 7}) == {
        "input_tokens": 21,
        "output_tokens": 7,
        "total_tokens": 28,
    }


def test_normalizes_reasoning_token_usage():
    assert normalize_usage({
        "prompt_tokens": 21,
        "completion_tokens": 7,
        "completion_tokens_details": {"reasoning_tokens": 5},
    })["reasoning_tokens"] == 5


def test_detects_qwen_models_for_no_thinking_parameter():
    assert is_qwen_model("qwen3.8-max")
    assert is_qwen_model("Qwen/Qwen3-32B")
    assert not is_qwen_model("gpt-4o-mini")


def test_detects_deepseek_v4_models_for_no_thinking_parameter():
    assert is_deepseek_v4_model("deepseek-v4-flash")
    assert is_deepseek_v4_model("deepseek/v4-pro")
    assert not is_deepseek_v4_model("deepseek-chat")
    assert thinking_parameters("deepseek-v4-flash") == {"thinking": {"type": "disabled"}}
    assert thinking_parameters("qwen3.8-max") == {"enable_thinking": False}
    assert thinking_parameters("gpt-4o-mini") == {}


def test_message_content_accepts_openai_content_parts():
    assert message_content({
        "content": [
            {"type": "text", "text": "first"},
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": " second"},
        ]
    }) == "first second"


def test_post_chat_retries_without_unsupported_optional_parameter(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"role": "assistant", "content": "{}"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            }).encode()

    def fake_urlopen(request, timeout):
        del timeout
        payload = json.loads(request.data.decode())
        requests.append(payload)
        if len(requests) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b'{"error":{"message":"unsupported parameter: thinking"}}'),
            )
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    message = post_chat(
        "https://api.example.test/v1",
        "deepseek-v4-flash",
        [{"role": "user", "content": "return JSON"}],
        timeout=5,
        thinking_options=thinking_parameters("deepseek-v4-flash"),
    )
    assert message_content(message) == "{}"
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert "thinking" not in requests[1]
    assert message["_timing"]["api_attempts"] == 2


def test_post_chat_keeps_unknown_models_on_standard_fields(monkeypatch):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"choices":[{"message":{"role":"assistant","content":"{}"}}]}'

    def fake_urlopen(request, timeout):
        del timeout
        requests.append(json.loads(request.data.decode()))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    post_chat(
        "https://api.example.test/v1",
        "some-openai-compatible-model",
        [{"role": "user", "content": "return JSON"}],
        timeout=5,
        thinking_options=thinking_parameters("some-openai-compatible-model"),
    )
    assert "response_format" in requests[0]
    assert "thinking" not in requests[0]
    assert "enable_thinking" not in requests[0]


def test_post_chat_recovers_from_empty_json_output(monkeypatch):
    requests = []

    class Response:
        def __init__(self, content):
            self.content = content

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "choices": [{"message": {"role": "assistant", "content": self.content}}]
            }).encode()

    def fake_urlopen(request, timeout):
        del timeout
        payload = json.loads(request.data.decode())
        requests.append(payload)
        return Response("" if len(requests) == 1 else "{}")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    message = post_chat(
        "https://api.example.test/v1",
        "deepseek-v4-flash",
        [{"role": "user", "content": "return JSON"}],
        timeout=5,
        thinking_options=thinking_parameters("deepseek-v4-flash"),
    )
    assert message_content(message) == "{}"
    assert "response_format" in requests[0]
    assert "response_format" not in requests[1]
    assert message["_timing"]["api_attempts"] == 2


def test_extracts_openai_tool_call():
    message = {
        "tool_calls": [
            {"function": {"name": "copy_file", "arguments": json.dumps({"source": "a", "destination": "b"})}}
        ]
    }
    assert extract_tool_call(message) == {
        "name": "copy_file",
        "arguments": {"source": "a", "destination": "b"},
    }


def test_extracts_qwen_xml_call():
    message = {
        "content": '<think></think><tool_call>{"name":"git_status","arguments":{"path":"."}}</tool_call>'
    }
    assert extract_tool_call(message)["name"] == "git_status"


def test_auto_backend_is_cross_platform_llama_cpp():
    config = Config.from_values(load_values(__import__("pathlib").Path("/missing")))
    assert resolve_local_backend(config) == "llama-cpp"


def test_suppresses_native_stderr_by_default(capfd, monkeypatch):
    monkeypatch.delenv("QSAY_LLAMA_LOG", raising=False)
    with suppress_native_output():
        os.write(2, b"native-noise\n")
    os.write(2, b"visible-error\n")
    _, stderr = capfd.readouterr()
    assert "native-noise" not in stderr
    assert "visible-error" in stderr


def test_api_provider_explains_initial_configuration():
    config = Config.from_values(load_values(Path("/missing-qsay-config")))
    with pytest.raises(RuntimeError) as error:
        APIProvider(config)
    message = str(error.value)
    assert "qsay config --global llm.model" in message
    assert "qsay --local" in message


def test_api_provider_explains_missing_key(monkeypatch):
    values = load_values(Path("/missing-qsay-config"))
    values["llm.model"] = "demo"
    values["llm.api-key"] = ""
    config = Config.from_values(values)
    # An environment variable must not be required or implicitly consumed.
    monkeypatch.setenv("QSAY_TEST_API_KEY", "secret")
    with pytest.raises(RuntimeError) as error:
        APIProvider(config)
    message = str(error.value)
    assert "qsay config --global llm.api-key YOUR_API_KEY" in message
    assert "环境变量" not in message
