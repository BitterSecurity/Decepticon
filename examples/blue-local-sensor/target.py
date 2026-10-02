import json
import os
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class Handler(BaseHTTPRequestHandler):
    def address_string(self) -> str:
        return "local"

    def respond(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self.respond(200, {"service": "blue-sensor-fixture"})
        elif path == "/admin":
            self.respond(403, {"error": "forbidden"})
        else:
            self.respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 0:
            self.rfile.read(min(length, 1024 * 1024))
        if urlsplit(self.path).path == "/login":
            self.respond(401, {"error": "invalid credentials"})
        else:
            self.respond(404, {"error": "not found"})


if __name__ == "__main__":
    socket_path = os.environ.get("BLUE_TARGET_SOCKET")
    if socket_path:
        path = Path(socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)

        class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

        server = UnixServer(str(path), Handler)
        path.chmod(0o666)
    else:
        server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.serve_forever()
