"""A local OpenAI-compatible stub server for exercising the real client over HTTP."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StubServer:
    """Serves ``/v1/chat/completions`` and ``/v1/models`` from scripted behaviour.

    ``plan`` is a list consumed one entry per chat request: either a reply string or an
    HTTP status code to fail with (e.g. 429, 500).
    """

    def __init__(self, plan: list, models: list[str] | None = None) -> None:
        self.plan = list(plan)
        self.models = models or ["nvidia/Nemotron-3_5-Lightning"]
        self.requests: list[dict] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    self._send(200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": "x"} for m in server.models]})
                else:
                    self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                server.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                if body.get("model") not in server.models:
                    self._send(404, {"error": {"message": f"model {body.get('model')} not found", "type": "not_found"}})
                    return
                step = server.plan.pop(0) if server.plan else "ok"
                if isinstance(step, int):
                    self._send(step, {"error": {"message": f"scripted {step}"}})
                    return
                self._send(
                    200,
                    {
                        "id": "cmpl-1",
                        "object": "chat.completion",
                        "created": 0,
                        "model": body["model"],
                        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": step}}],
                        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
                    },
                )

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/v1/"

    def __enter__(self) -> "StubServer":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
