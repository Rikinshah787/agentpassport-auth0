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


def test_gmail_uses_the_google_connection_and_shows_as_gmail(client, agent, connect, fake):
    connect("gmail")
    assert fake.pending.connection == "google-oauth2"
    assert fake.pending.scopes == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert "✓ Gmail" in client.get("/").get_data(as_text=True)


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
    assert b"Rescan for agents" in client.get("/").data


def test_removing_an_agent_explains_how_to_add_it_back(client, home, login):
    login()
    agents.issue()
    response = client.post("/agents/Codex/remove")
    assert "Codex" not in agents.load()
    assert b"Use step 3 to add it back" in client.get(response.headers["Location"]).data


def test_rescan_adds_back_only_the_removed_agent_and_says_so(client, home, login):
    login()
    agents.issue()
    client.post("/agents/Codex/remove")
    page = client.post("/agents/issue").get_data(as_text=True)
    assert "Passports issued to: Codex." in page and "Claude Desktop." not in page
    assert "Codex" in agents.load()


def test_rescan_with_nothing_missing_says_so(client, home, login):
    login()
    agents.issue()
    keys = {name: passport["key"] for name, passport in agents.load().items()}
    page = client.post("/agents/issue").get_data(as_text=True)
    assert "no agents without a passport" in page
    assert {name: passport["key"] for name, passport in agents.load().items()} == keys


def test_rescan_needs_a_login(client, home):
    response = client.post("/agents/issue")
    assert response.status_code == 302 and not agents.STORE.exists()


def test_disconnect_removes_the_account_and_the_cached_token(agent, call, client, fake):
    assert call(REPOS, agent).status_code == 200  # the SDK now holds a copy of the app token
    response = client.post("/disconnect/github")
    assert response.headers["Location"].endswith("disconnected=1")
    assert "github" not in fake.connected
    before = len(fake.app_calls)
    assert call(REPOS, agent).status_code == 502
    assert len(fake.app_calls) == before
    assert "✓ GitHub" not in client.get("/").get_data(as_text=True)


def test_disconnect_leaves_the_other_app_alone(agent, connect, client, fake):
    connect("gmail")
    client.post("/disconnect/github")
    assert fake.connected == {"google-oauth2"}
    assert "✓ Gmail" in client.get("/").get_data(as_text=True)


def test_another_website_cannot_disconnect_an_app(agent, client, fake):
    assert client.post("/disconnect/github", headers={"Origin": "https://evil.example"}).status_code == 403
    assert "github" in fake.connected


def test_unknown_app_cannot_be_disconnected(client):
    assert client.post("/disconnect/slack").status_code == 404


def test_login_asks_a_logged_out_user_to_sign_in_again(login):
    assert login()["prompt"] == ["login"]


def test_logout_clears_the_session_but_keeps_connected_apps(agent, call, client, fake):
    call(REPOS, agent)
    client.get("/logout")
    assert call(REPOS, agent).status_code == 401
    page = client.get("/").get_data(as_text=True)
    assert "Log in with Auth0" in page and "Read repos" in page
    assert "<time>" not in client.get("/activity").get_data(as_text=True)
    assert "github" in fake.connected


def test_panel_never_shows_agent_keys(client, home, login):
    login()
    agents.issue()
    page = client.get("/").get_data(as_text=True)
    assert all(passport["key"] not in page for passport in agents.load().values())
