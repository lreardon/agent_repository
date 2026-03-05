#!/usr/bin/env python3
"""
HTTP signer proxy for k6 load tests.

k6 doesn't support Ed25519 natively, so this lightweight Flask/http.server
accepts requests from k6, signs them, and forwards to the real API.

Usage:
  python3 load-tests/signer.py [listen_port] [target_url]

  Defaults: port=9999, target=http://localhost:8080

k6 sends requests to http://localhost:9999 with headers:
  X-Agent-Id: <agent_id>
  X-Private-Key: <private_key_hex>

The proxy signs the request and forwards it to the target API.
"""

import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import httpx
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
TARGET_URL = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8080"

client = httpx.Client(timeout=30)


def sign_request(private_key_hex: str, timestamp: str, method: str, path: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{timestamp}\n{method}\n{path}\n{body_hash}".encode()
    signing_key = SigningKey(private_key_hex.encode(), encoder=HexEncoder)
    signed = signing_key.sign(message, encoder=HexEncoder)
    return signed.signature.decode()


class SignerHandler(BaseHTTPRequestHandler):
    def _handle(self, method: str):
        # Read body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # Get agent credentials from headers
        agent_id = self.headers.get("X-Agent-Id", "")
        private_key = self.headers.get("X-Private-Key", "")

        # Build signed headers
        timestamp = datetime.now(UTC).isoformat()
        nonce = uuid.uuid4().hex
        signature = sign_request(private_key, timestamp, method, self.path, body)

        headers = {
            "Authorization": f"AgentSig {agent_id}:{signature}",
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "Content-Type": "application/json",
        }

        # Forward to target
        url = f"{TARGET_URL}{self.path}"
        resp = client.request(method, url, content=body, headers=headers)

        # Return response to k6
        self.send_response(resp.status_code)
        for k, v in resp.headers.items():
            if k.lower() not in ("transfer-encoding", "content-encoding", "content-length"):
                self.send_header(k, v)
        resp_body = resp.content
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")

    def log_message(self, format, *args):
        pass  # Suppress default logging for performance


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", LISTEN_PORT), SignerHandler)
    print(f"🔑 Signer proxy listening on :{LISTEN_PORT} → {TARGET_URL}")
    server.serve_forever()
