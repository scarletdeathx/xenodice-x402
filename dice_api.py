"""dice-api: HTTP dice rolls backed by Eshkol randomness.

A tiny bridge between the Eshkol draw daemon and anything that speaks HTTP.
The game server calls this; it never touches randomness internals.

  GET /roll?sides=20&count=2&backend=moonlab

  -> {"rolls": [14, 7], "sides": 20, "count": 2,
      "provenance": {"backend": "moonlab", "n_bytes": 8,
                     "drawn_at": "2026-10-05T...", "attestation": null}}

Bytes come from drawd (Unix socket preferred, TCP fallback). Dice mapping
uses rejection sampling for uniformity -- never modulo.

Run: python -m dice_api.dice_api [--host 127.0.0.1] [--port 18752]
"""

import argparse
import json
import socket
import struct
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

DRAWD_SOCKET = "/tmp/eshkol-drawd.sock"
DRAWD_HOST = "127.0.0.1"
DRAWD_PORT = 18751
ERR = 0xFFFFFFFF


def draw_bytes(backend: str, n: int) -> bytes:
    """Get n random bytes from drawd. Unix socket first, TCP fallback."""
    req = (json.dumps({"backend": backend, "n": n}) + "\n").encode()
    # Try Unix socket
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(DRAWD_SOCKET)
    except OSError:
        s = socket.create_connection((DRAWD_HOST, DRAWD_PORT), timeout=30)
    with s:
        s.sendall(req)
        # drawd sends raw bytes (no length prefix on this path per client.py usage)
        # Actually: read exactly n bytes
        buf = b""
        while len(buf) < n:
            chunk = s.recv(min(65536, n - len(buf)))
            if not chunk:
                break
            buf += chunk
    if len(buf) != n:
        raise RuntimeError(f"drawd returned {len(buf)} bytes, wanted {n}")
    return buf


def roll_dice(data: bytes, sides: int, count: int) -> list[int]:
    """Map random bytes to uniform dice rolls via rejection sampling."""
    if sides < 2 or sides > 256:
        raise ValueError("sides must be 2-256")
    if count < 1 or count > 100:
        raise ValueError("count must be 1-100")
    # Rejection threshold: largest multiple of sides <= 256
    limit = 256 - (256 % sides)
    rolls = []
    idx = 0
    while len(rolls) < count:
        if idx >= len(data):
            raise RuntimeError("ran out of random bytes (increase draw size)")
        b = data[idx]
        idx += 1
        if b < limit:
            rolls.append((b % sides) + 1)
    return rolls


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # quiet

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/roll":
            self.send_error(404, "only /roll is supported")
            return
        q = urllib.parse.parse_qs(parsed.query)
        try:
            sides = int(q.get("sides", ["20"])[0])
            count = int(q.get("count", ["1"])[0])
            backend = q.get("backend", ["synthetic"])[0]
            # Over-provision bytes: rejection sampling discards some
            n_bytes = count * 4
            raw = draw_bytes(backend, n_bytes)
            rolls = roll_dice(raw, sides, count)
            body = json.dumps({
                "rolls": rolls,
                "sides": sides,
                "count": count,
                "provenance": {
                    "backend": backend,
                    "n_bytes": n_bytes,
                    "note": "bytes from Eshkol via drawd; see drawd provenance for attestation",
                },
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (ValueError, RuntimeError) as e:
            self.send_error(400, str(e))
        except Exception as e:
            self.send_error(502, f"drawd error: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=18752)
    args = ap.parse_args()
    srv = HTTPServer((args.host, args.port), Handler)
    print(f"dice-api on {args.host}:{args.port} (drawd via {DRAWD_SOCKET})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
