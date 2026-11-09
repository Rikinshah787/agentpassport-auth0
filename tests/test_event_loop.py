"""Regression test for "RuntimeError: Event loop is closed" on every second login.

Flask runs each async request on its own event loop, but the Auth0 SDK keeps one
shared HTTP client. This uses a real local connection because a faked call can't
show the bug.
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_passport.auth import auth0


class TokenEndpoint(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, as a real token endpoint would allow

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        body = json.dumps({"access_token": "t", "token_type": "Bearer", "expires_in": 60}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def token_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), TokenEndpoint)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/oauth/token"
    server.shutdown()
    server.server_close()


def test_repeated_logins_work_across_event_loops(token_url):
    for _ in range(4):
        # Each asyncio.run is a new event loop, like each Flask request.
        token = asyncio.run(
            auth0._oauth.fetch_token(token_url, code="c", grant_type="authorization_code", redirect_uri="http://x/cb")
        )
        assert token["access_token"] == "t"
