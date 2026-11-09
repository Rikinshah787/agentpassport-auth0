"""The decisions: who may do what, with real RS256 tokens checked by the real verifier."""
import pytest

from agent_passport import agents
from conftest import ATTACKER_KEY

REPOS = "github_list_repos"
INBOX = "gmail_list_messages"


@pytest.fixture
def agent(home, login, connect):
    """A logged-in user with GitHub connected, and one agent holding only github:read."""
    login()
    connect("github")
    agents.issue()
    return agents.load()["Codex"]["key"]


def test_no_key_and_wrong_key_are_refused(client, call):
    assert call(REPOS, None).status_code == 401
    assert call(REPOS, "wrong").status_code == 401


def test_nobody_logged_in_is_refused(client, home, call):
    agents.issue()
    response = call(REPOS, agents.load()["Codex"]["key"])
    assert response.status_code == 401 and "logged in" in response.get_json()["message"]


def test_allowed_call_returns_real_data_and_never_the_token(agent, call):
    response = call(REPOS, agent)
    assert response.status_code == 200
    assert response.get_json()["data"]["repos"] == ["octo/one", "octo/two"]
    assert "APP-SECRET-TOKEN" not in response.get_data(as_text=True)


def test_passport_without_the_scope_is_refused_and_app_is_never_called(agent, call, fake):
    before = len(fake.app_calls)
    response = call(INBOX, agent)
    assert response.status_code == 403 and "passport" in response.get_json()["message"]
    assert len(fake.app_calls) == before


def test_switch_takes_effect_on_the_next_call(agent, call):
    assert call(REPOS, agent).status_code == 200
    agents.set_scopes("Codex", [])
    assert call(REPOS, agent).status_code == 403
    agents.set_scopes("Codex", ["github:read"])
    assert call(REPOS, agent).status_code == 200


def test_changing_one_agent_does_not_affect_another(agent, call):
    other = agents.load()["Claude Desktop"]["key"]
    agents.set_scopes("Codex", [])
    assert call(REPOS, agent).status_code == 403
    assert call(REPOS, other).status_code == 200


def test_the_key_decides_identity_not_the_request_body(agent, client):
    agents.set_scopes("Codex", [])
    response = client.post(f"/api/tools/{REPOS}", headers={"X-Passport-Key": agent}, json={"agent": "Claude Desktop"})
    assert response.status_code == 403 and "Codex" in response.get_json()["message"]


def test_scope_missing_from_the_token_is_refused(home, login, connect, call):
    login("github:read")
    connect("github")
    agents.issue()
    agents.set_scopes("Codex", ["gmail:read"])
    response = call(INBOX, agents.load()["Codex"]["key"])
    assert response.status_code == 403 and "token" in response.get_json()["message"]


@pytest.mark.parametrize(
    "token_options",
    [{"key": ATTACKER_KEY}, {"audience": "https://another-api.test"}, {"lifetime": -60}],
    ids=["forged", "wrong-audience", "expired"],
)
def test_bad_tokens_are_refused_and_app_is_never_called(home, login, call, fake, token_options):
    login(**token_options)
    agents.issue()
    response = call(REPOS, agents.load()["Codex"]["key"])
    assert response.status_code == 401 and fake.app_calls == []


def test_app_not_connected_fails_closed(home, login, call):
    login()
    agents.issue()
    response = call(REPOS, agents.load()["Codex"]["key"])
    assert response.status_code == 502 and response.get_json()["data"] is None


def test_app_down_fails_closed(agent, call, fake):
    fake.apps_up = False
    response = call(REPOS, agent)
    assert response.status_code == 502 and response.get_json()["data"] is None


def test_gmail_returns_sender_and_subject_only(agent, call, connect):
    connect("gmail")
    agents.set_scopes("Codex", ["gmail:read"])
    response = call(INBOX, agent)
    assert response.status_code == 200
    assert response.get_json()["data"]["messages"] == [{"from": "a@example.com", "subject": "Hello"}]
    text = response.get_data(as_text=True)
    assert "BODY-MUST-NOT-LEAK" not in text and "DROP-ME" not in text


def test_login_requests_a_refresh_token_and_only_known_scopes(login):
    params = login("github:read admin:everything")
    scope = params["scope"][0].split()
    assert "offline_access" in scope and "github:read" in scope and "admin:everything" not in scope


def test_other_websites_cannot_trigger_panel_actions(client, home, login):
    login()
    assert client.post("/agents/issue", headers={"Origin": "https://evil.example"}).status_code == 403
    assert not agents.STORE.exists()


def test_rebinding_host_is_refused(client):
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403


def test_panel_shows_each_setup_stage(client, home, login, connect):
    assert b"Log in with Auth0" in client.get("/").data
    login()
    assert b"Find local agents" in client.get("/").data and b"Connect GitHub" in client.get("/").data
    connect("github")
    assert "✓ GitHub" in client.get("/").get_data(as_text=True)
    client.post("/agents/issue")
    assert b"Scan for new agents" in client.get("/").data


def test_panel_never_shows_agent_keys(client, home, login):
    login()
    agents.issue()
    page = client.get("/").get_data(as_text=True)
    assert all(passport["key"] not in page for passport in agents.load().values())
