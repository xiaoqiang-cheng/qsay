"""Small OpenAI-compatible mock used for local provider smoke tests."""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self.send_error(400, "invalid JSON")
            return
        if "messages" not in body:
            self.send_error(400, "missing messages")
            return
        model = str(body.get("model", "")).lower()
        if model.startswith("qwen") and body.get("enable_thinking") is not False:
            self.send_error(400, "qwen thinking was not disabled")
            return
        if model.startswith("deepseek-v4") and body.get("thinking") != {"type": "disabled"}:
            self.send_error(400, "deepseek-v4 thinking was not disabled")
            return
        user = body["messages"][-1].get("content", "")
        if body.get("tools"):
            if "解压" in user or "extract" in user.lower():
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "mock-call",
                        "type": "function",
                        "function": {
                            "name": "extract_archive",
                            "arguments": json.dumps({"source": "backup.tar", "destination": "./backup"}),
                        },
                    }],
                }
            else:
                message = {"role": "assistant", "content": "NO_CALL"}
        else:
            plan = {
                "intent": "file.list",
                "command": "printf api-ok",
                "shell": "sh",
                "risk": "read_only",
                "assumptions": [],
                "tools": ["printf"],
                "clarification": None,
            }
            message = {"role": "assistant", "content": json.dumps(plan)}
        data = json.dumps({
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18765
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
