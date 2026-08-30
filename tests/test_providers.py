import json
import os

from dox.providers import chat_endpoint, extract_tool_call, resolve_local_backend, suppress_native_output
from dox.config import Config, load_values


def test_chat_endpoint():
    assert chat_endpoint("https://example.test/v1") == "https://example.test/v1/chat/completions"
    full = "https://example.test/v1/chat/completions"
    assert chat_endpoint(full) == full


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
