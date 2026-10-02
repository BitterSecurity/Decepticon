import json
import os
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DATA_DIR = Path("/tmp/blue-sensor-fixture")
PUBLIC_DIR = DATA_DIR / "public"


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
        parsed = urlsplit(self.path)
        path = parsed.path
        if path == "/":
            self.respond(200, {"service": "blue-sensor-fixture", "routes": ["/read?name=welcome.txt", "/login", "/admin"]})
        elif path == "/read":
            name = parse_qs(parsed.query).get("name", ["welcome.txt"])[0]
            try:
                content = (PUBLIC_DIR / name).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                self.respond(404, {"error": "not found"})
            else:
                self.respond(200, {"content": content})
        elif path == "/admin":
            self.respond(403, {"error": "forbidden"})
        else:
            self.respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        while length > 0:
            chunk = self.rfile.read(min(length, 64 * 1024))
            if not chunk:
                break
            length -= len(chunk)
        if urlsplit(self.path).path == "/login":
            self.respond(401, {"error": "invalid credentials"})
        else:
            self.respond(404, {"error": "not found"})


if __name__ == "__main__":
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    (PUBLIC_DIR / "welcome.txt").write_text("Welcome to the Blue Cell fixture.\n", encoding="utf-8")
    (DATA_DIR / "secret.txt").write_text("blue-cell-test-canary-20261002\n", encoding="utf-8")
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
