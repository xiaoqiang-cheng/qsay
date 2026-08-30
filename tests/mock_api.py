"""Small OpenAI-compatible mock used for local provider smoke tests."""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if b"messages" not in body:
            self.send_error(400, "missing messages")
            return
        plan = {
            "intent": "file.list",
            "command": "printf api-ok",
            "shell": "sh",
            "risk": "read_only",
            "assumptions": [],
            "tools": ["printf"],
            "clarification": None,
        }
        data = json.dumps({"choices": [{"message": {"content": json.dumps(plan)}}]}).encode()
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
