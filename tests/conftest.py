"""Shared test setup. Runs the real app, SDKs and token checks; only network edges are faked.

Nothing here can touch a real .env or a real app config: dotenv is disabled and every
client path points into a temporary folder.
"""
import os
import tempfile
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import dotenv
import pytest
from authlib.jose import JsonWebKey, jwt

dotenv.load_dotenv = lambda *args, **kwargs: False
os.environ["HOME"] = tempfile.mkdtemp()

ISSUER = "https://test-tenant.example/"
AUDIENCE = "https://agent-passport.test"
os.environ.update(
    AUTH0_DOMAIN="test-tenant.example",
    AUTH0_CLIENT_ID="test-client",
    AUTH0_CLIENT_SECRET="test-secret",
    AUTH0_SECRET="x" * 64,
    AUTH0_REDIRECT_URI="http://localhost/callback",
    AUTH0_AUDIENCE=AUDIENCE,
)

from agent_passport import agents, app as app_module  # noqa: E402
from agent_passport.auth import auth0, state_store, transaction_store  # noqa: E402

SIGNING_KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "test"})
ATTACKER_KEY = JsonWebKey.generate_key("RSA", 2048, is_private=True, options={"kid": "test"})


def make_token(scope, key=SIGNING_KEY, audience=AUDIENCE, lifetime=3600):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": audience, "sub": "auth0|test", "iat": now - 5, "exp": now + lifetime, "scope": scope}
    return jwt.encode({"alg": "RS256", "kid": "test", "typ": "JWT"}, claims, key).decode()


class FakeAuth0:
    """Stands in for Auth0 and the connected apps at the network edge."""

    def __init__(self):
        self.scope = ""
        self.token_options = {}
        self.connected = set()
        self.pending = None
        self.app_calls = []
        self.apps_up = True

    def http_get(self, url, **kwargs):
        self.app_calls.append(url)
        if not self.apps_up:
            def down():
                raise RuntimeError("app is down")
            return SimpleNamespace(raise_for_status=down)
        if url.endswith("/user/repos"):
            body = [{"full_name": "octo/one"}, {"full_name": "octo/two", "private": True}]
        elif url.endswith("/messages"):
            body = {"messages": [{"id": "m1"}]}
        else:
            headers = [{"name": "From", "value": "a@example.com"}, {"name": "Subject", "value": "Hello"}, {"name": "X-Other", "value": "DROP-ME"}]
            body = {"snippet": "BODY-MUST-NOT-LEAK", "payload": {"headers": headers}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


@pytest.fixture
def fake(monkeypatch):
    fake_auth0 = FakeAuth0()

    async def metadata(domain):
        return {"issuer": ISSUER, "authorization_endpoint": ISSUER + "authorize", "token_endpoint": ISSUER + "oauth/token"}

    async def fetch_token(url, **kwargs):
        return {
            "access_token": make_token(fake_auth0.scope, **fake_auth0.token_options),
            "refresh_token": "test-refresh",
            "scope": fake_auth0.scope,
            "expires_in": 3600,
            "userinfo": {"sub": "auth0|test", "email": "person@example.com"},
        }

    async def verifier_fetch(url, *args, **kwargs):
        if "openid-configuration" in url:
            return {"issuer": ISSUER, "jwks_uri": ISSUER + ".well-known/jwks.json"}
        return {"keys": [SIGNING_KEY.as_dict(is_private=False, kid="test")]}

    async def refresh(options):
        return {"access_token": "my-account-token", "scope": options.get("scope", ""), "expires_in": 3600}

    async def connect_account(access_token, request):
        fake_auth0.pending = request
        return SimpleNamespace(auth_session="s", connect_uri=ISSUER + "connect", connect_params=SimpleNamespace(ticket="t"))

    async def complete_connect(access_token, request):
        fake_auth0.connected.add(fake_auth0.pending.connection)
        return SimpleNamespace(app_state=None, connection=fake_auth0.pending.connection)

    async def list_accounts(connection=None, from_param=None, take=None, store_options=None):
        return SimpleNamespace(accounts=[SimpleNamespace(connection=name) for name in fake_auth0.connected])

    async def token_for_connection(options):
        if options["connection"] not in fake_auth0.connected:
            raise RuntimeError("not connected")
        return {"access_token": "APP-SECRET-TOKEN", "scope": "", "expires_in": 3600}

    monkeypatch.setattr(auth0, "_get_oidc_metadata_cached", metadata)
    monkeypatch.setattr(auth0._oauth, "fetch_token", fetch_token)
    monkeypatch.setattr(auth0, "get_token_by_refresh_token", refresh)
    monkeypatch.setattr(auth0, "get_token_for_connection", token_for_connection)
    monkeypatch.setattr(auth0, "list_connected_accounts", list_accounts)
    monkeypatch.setattr(auth0._my_account_client, "connect_account", connect_account)
    monkeypatch.setattr(auth0._my_account_client, "complete_connect_account", complete_connect)
    monkeypatch.setattr(app_module.verifier.options, "custom_fetch", verifier_fetch)
    monkeypatch.setattr(app_module.httpx, "get", fake_auth0.http_get)
    return fake_auth0


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Point every client at a temporary folder. Only Claude Desktop and Codex are 'installed'."""
    desktop = tmp_path / "Claude" / "config.json"
    codex = tmp_path / ".codex" / "config.toml"
    desktop.parent.mkdir()
    codex.parent.mkdir()
    clients = {
        "Claude Desktop": (desktop, "mcpServers", "no-such-process-1"),
        "Codex": (codex, None, "no-such-process-2"),
        "Cursor": (tmp_path / ".cursor" / "mcp.json", "mcpServers", "no-such-process-3"),
    }
    monkeypatch.setattr(agents, "CLIENTS", clients)
    monkeypatch.setattr(agents, "STORE", tmp_path / "passports.json")
    return SimpleNamespace(root=tmp_path, desktop=desktop, codex=codex, cursor=clients["Cursor"][0])


@pytest.fixture
def client(fake, home):
    state_store._data.clear()
    transaction_store._data.clear()
    app_module.ACTIVITY.clear()
    app_module.CONNECTED.update(checked=False, services=set())
    return app_module.app.test_client()


@pytest.fixture
def login(client, fake):
    def _login(scope="github:read gmail:read", **token_options):
        fake.token_options = token_options
        query = "&".join(f"scope={name}" for name in scope.split())
        location = client.get(f"/login?{query}").headers["Location"]
        params = parse_qs(urlparse(location).query)
        fake.scope = params["scope"][0]
        response = client.get("/callback", query_string={"state": params["state"][0], "code": "c"})
        assert response.status_code == 302
        return params

    return _login


@pytest.fixture
def connect(client, fake):
    def _connect(service):
        assert client.get(f"/connect/{service}").status_code == 302
        response = client.get("/callback", query_string={"state": fake.pending.state, "connect_code": "c"})
        assert response.headers["Location"].endswith("connected=1")

    return _connect


@pytest.fixture
def call(client):
    def _call(tool, key):
        headers = {"X-Passport-Key": key} if key else {}
        return client.post(f"/api/tools/{tool}", headers=headers, json={})

    return _call
