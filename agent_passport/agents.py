"""Finds local AI agents and issues each one a passport."""
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "passports.json"
SERVER = ROOT / "mcp" / "server.mjs"
HOME = Path.home()
MCP_NAME = "agent-passport"
DEFAULT_SCOPES = ["github:read"]

# name: (its MCP config file, the key holding its servers, text that identifies its process)
CLIENTS = {
    "Claude Code": (ROOT / ".mcp.json", "mcpServers", "claude-code"),
    "Claude Desktop": (HOME / "Library/Application Support/Claude/claude_desktop_config.json", "mcpServers", "Claude.app"),
    "VS Code": (HOME / "Library/Application Support/Code/User/mcp.json", "servers", "Visual Studio Code.app"),
    "Cursor": (HOME / ".cursor/mcp.json", "mcpServers", "Cursor.app"),
    "Windsurf": (HOME / ".codeium/windsurf/mcp_config.json", "mcpServers", "Windsurf.app"),
    "Codex": (HOME / ".codex/config.toml", None, "codex"),
}


def load():
    return json.loads(STORE.read_text()) if STORE.exists() else {}


def save(store):
    write_private(STORE, json.dumps(store, indent=2))


def write_private(path, text):
    # Write to a temp file then swap, so a crash can't leave a half-written file.
    temp = path.with_name(path.name + ".tmp")
    handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as file:
        file.write(text)
    os.replace(temp, path)


def scopes():
    return {name: passport["scopes"] for name, passport in load().items()}


def set_scopes(name, new_scopes):
    store = load()
    store[name]["scopes"] = new_scopes
    save(store)


def agent_for_key(key):
    # The key decides who is calling, not a name the caller claims.
    for name, passport in load().items():
        if hmac.compare_digest(passport["key"], key):
            return name
    return None


def find():
    # Installed means its config folder exists. Running means its process is alive.
    processes = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout
    found = {}
    for name, (path, _, process) in CLIENTS.items():
        if path.parent.exists() or process in processes:
            found[name] = process in processes
    return found


def backup(path):
    target = path.with_name(path.name + ".passport-backup")
    if path.exists() and not target.exists():
        shutil.copy2(path, target)


def toml_block(name):
    return (
        f"\n[mcp_servers.{MCP_NAME}]\n"
        f"command = {json.dumps(shutil.which('node') or 'node')}\n"
        f"args = {json.dumps([str(SERVER)])}\n"
        f"env = {{ AGENT_NAME = {json.dumps(name)} }}\n"
    )


TOML_ENTRY = re.compile(rf"\n?\[mcp_servers\.{MCP_NAME}\]\n(?:(?!\[).*\n?)*")


def wire(name):
    path, key, _ = CLIENTS[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    backup(path)
    text = path.read_text() if path.exists() else ""
    if key is None:
        write_private(path, TOML_ENTRY.sub("", text) + toml_block(name))
        return
    config = json.loads(text) if text.strip() else {}  # bad JSON raises, so we never overwrite it
    config.setdefault(key, {})[MCP_NAME] = {
        "command": shutil.which("node") or "node",
        "args": [str(SERVER)],
        "env": {"AGENT_NAME": name},
    }
    write_private(path, json.dumps(config, indent=2) + "\n")


def unwire(name):
    path, key, _ = CLIENTS[name]
    if not path.exists():
        return
    text = path.read_text()
    if key is None:
        write_private(path, TOML_ENTRY.sub("", text))
        return
    config = json.loads(text)
    config.get(key, {}).pop(MCP_NAME, None)
    write_private(path, json.dumps(config, indent=2) + "\n")


def issue():
    """One click: find every known agent, give it a passport, wire it in."""
    store = load()
    issued, skipped = [], []
    for name, is_running in find().items():
        try:
            wire(name)
        except (OSError, ValueError) as error:
            skipped.append(f"{name} ({error})")
            continue
        store.setdefault(name, {"key": secrets.token_hex(32), "scopes": list(DEFAULT_SCOPES)})
        issued.append(f"{name} (running)" if is_running else name)
    save(store)
    return issued, skipped


def remove(name):
    if name in CLIENTS:
        unwire(name)
    store = load()
    store.pop(name, None)
    save(store)
