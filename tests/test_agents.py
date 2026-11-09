"""Finding agents and writing their configs. Mistakes here would damage someone's real settings."""
import json
import stat

from agent_passport import agents


def seed_desktop(home):
    home.desktop.write_text(json.dumps({"theme": "keep", "mcpServers": {"other": {"env": {"SECRET": "keep"}}}}))


def test_only_installed_clients_are_found(home):
    assert sorted(agents.find()) == ["Claude Desktop", "Codex"]


def test_issue_gives_each_agent_its_own_key(home):
    issued, skipped = agents.issue()
    store = agents.load()
    assert sorted(issued) == ["Claude Desktop", "Codex"] and skipped == []
    assert store["Claude Desktop"]["key"] != store["Codex"]["key"]


def test_store_is_private_to_this_user(home):
    agents.issue()
    assert stat.S_IMODE(agents.STORE.stat().st_mode) == 0o600


def test_wiring_keeps_existing_settings_and_writes_no_key(home):
    seed_desktop(home)
    agents.issue()
    config = json.loads(home.desktop.read_text())
    assert config["theme"] == "keep" and config["mcpServers"]["other"]["env"]["SECRET"] == "keep"
    entry = config["mcpServers"][agents.MCP_NAME]
    assert entry["env"] == {"AGENT_NAME": "Claude Desktop"}
    assert agents.load()["Claude Desktop"]["key"] not in home.desktop.read_text()


def test_backup_keeps_the_original_even_after_a_second_click(home):
    seed_desktop(home)
    original = home.desktop.read_text()
    agents.issue()
    agents.issue()
    assert (home.desktop.parent / "config.json.passport-backup").read_text() == original


def test_second_click_keeps_keys_and_does_not_duplicate(home):
    home.codex.write_text('model = "keep"\n\n[profiles.fast]\nmodel = "o"\n')
    agents.issue()
    keys = {name: passport["key"] for name, passport in agents.load().items()}
    agents.issue()
    assert {name: passport["key"] for name, passport in agents.load().items()} == keys
    text = home.codex.read_text()
    assert text.count(f"[mcp_servers.{agents.MCP_NAME}]") == 1
    assert 'model = "keep"' in text and "[profiles.fast]" in text


def test_a_broken_config_is_reported_and_left_alone(home):
    home.desktop.write_text("{ not json")
    issued, skipped = agents.issue()
    assert home.desktop.read_text() == "{ not json"
    assert "Claude Desktop" not in issued and skipped[0].startswith("Claude Desktop")
    assert "Claude Desktop" not in agents.load()


def test_remove_takes_out_only_our_entry(home):
    seed_desktop(home)
    home.codex.write_text('model = "keep"\n\n[profiles.fast]\nmodel = "o"\n')
    agents.issue()
    agents.remove("Claude Desktop")
    agents.remove("Codex")
    config = json.loads(home.desktop.read_text())
    assert agents.MCP_NAME not in config["mcpServers"] and "other" in config["mcpServers"]
    text = home.codex.read_text()
    assert agents.MCP_NAME not in text and 'model = "keep"' in text and "[profiles.fast]" in text
    assert agents.load() == {}


def test_a_removed_agent_has_no_key(home):
    agents.issue()
    key = agents.load()["Codex"]["key"]
    assert agents.agent_for_key(key) == "Codex"
    agents.remove("Codex")
    assert agents.agent_for_key(key) is None


def test_rescan_adds_a_removed_agent_back_with_a_fresh_passport(home):
    agents.issue()
    agents.set_scopes("Codex", ["github:read", "gmail:read"])
    old_key = agents.load()["Codex"]["key"]
    agents.remove("Codex")

    issued, skipped = agents.issue()

    passport = agents.load()["Codex"]
    assert issued == ["Codex"] and skipped == []
    assert passport["scopes"] == agents.DEFAULT_SCOPES
    assert agents.agent_for_key(passport["key"]) == "Codex" and agents.agent_for_key(old_key) is None
    assert agents.MCP_NAME in home.codex.read_text()


def test_rescan_reports_only_what_it_added_and_keeps_existing_passports(home):
    agents.issue()
    agents.set_scopes("Codex", [])
    issued, skipped = agents.issue()
    assert issued == [] and skipped == []
    assert agents.load()["Codex"]["scopes"] == []
