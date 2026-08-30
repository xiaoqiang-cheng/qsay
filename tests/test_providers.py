import json
import os
from pathlib import Path

import pytest

from dox.providers import APIProvider, chat_endpoint, extract_tool_call, is_qwen_model, normalize_usage, resolve_local_backend, suppress_native_output
from dox.config import Config, load_values


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
    monkeypatch.delenv("DOX_LLAMA_LOG", raising=False)
    with suppress_native_output():
        os.write(2, b"native-noise\n")
    os.write(2, b"visible-error\n")
    _, stderr = capfd.readouterr()
    assert "native-noise" not in stderr
    assert "visible-error" in stderr


def test_api_provider_explains_initial_configuration():
    config = Config.from_values(load_values(Path("/missing-dox-config")))
    with pytest.raises(RuntimeError) as error:
        APIProvider(config)
    message = str(error.value)
    assert "dox config --global llm.model" in message
    assert "dox --local" in message


def test_api_provider_explains_missing_key(monkeypatch):
    values = load_values(Path("/missing-dox-config"))
    values["llm.model"] = "demo"
    values["llm.api-key"] = ""
    config = Config.from_values(values)
    # An environment variable must not be required or implicitly consumed.
    monkeypatch.setenv("DOX_TEST_API_KEY", "secret")
    with pytest.raises(RuntimeError) as error:
        APIProvider(config)
    message = str(error.value)
    assert "dox config --global llm.api-key YOUR_API_KEY" in message
    assert "环境变量" not in message
