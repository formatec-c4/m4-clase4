import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            status = 200
            payload = {"status": "ok"}
        else:
            status = 200
            payload = {
                "app": "formatec-security-lab",
                "message": "Imagen construida y ejecutándose",
            }

        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print("Servidor escuchando en http://0.0.0.0:8080", flush=True)
    server.serve_forever()
