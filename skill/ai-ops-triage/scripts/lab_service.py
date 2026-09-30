"""Run a deliberately broken AI app and a healthy model API stub on loopback."""

from __future__ import annotations

import argparse
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from ops_lab import checked_root, read_json


def respond(handler: BaseHTTPRequestHandler, status: int, body: dict) -> None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def make_model_handler(model_id: str):
    class ModelHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/v1/models":
                respond(self, 200, {"data": [{"id": model_id}]})
            elif self.path == "/_live":
                respond(self, 200, {"status": "live"})
            else:
                respond(self, 404, {"error": "not_found"})

        def log_message(self, format: str, *args: object) -> None:
            pass

    return ModelHandler


def make_app_handler(root: Path):
    class AppHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/_live":
                respond(self, 200, {"status": "live"})
                return
            if self.path != "/health":
                respond(self, 404, {"error": "not_found"})
                return
            config = read_json(root / "config.json")
            parsed = urlparse(config["model_url"])
            if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
                reason = "model_endpoint_not_loopback"
            else:
                try:
                    with urlopen(config["model_url"] + "/v1/models", timeout=1) as response:
                        models = json.load(response)["data"]
                    reason = "ok" if any(item.get("id") == config["model_id"] for item in models) else "model_not_found"
                except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError):
                    reason = "model_endpoint_unreachable"
            with (root / "app.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{datetime.now(timezone.utc).isoformat()} health={reason}\n")
            respond(self, 200 if reason == "ok" else 503, {"status": "healthy" if reason == "ok" else "unhealthy", "reason": reason})

        def log_message(self, format: str, *args: object) -> None:
            pass

    return AppHandler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = checked_root(args.root)
    inventory = read_json(root / "inventory.json")
    app_port = urlparse(inventory["app_url"]).port
    model_port = urlparse(inventory["model_url"]).port
    model_server = ThreadingHTTPServer(("127.0.0.1", model_port), make_model_handler(inventory["model_id"]))
    app_server = ThreadingHTTPServer(("127.0.0.1", app_port), make_app_handler(root))
    threading.Thread(target=model_server.serve_forever, daemon=True).start()
    try:
        print(json.dumps({"app_url": inventory["app_url"], "model_url": inventory["model_url"]}), flush=True)
        app_server.serve_forever()
    finally:
        app_server.server_close()
        model_server.shutdown()
        model_server.server_close()


if __name__ == "__main__":
    main()
