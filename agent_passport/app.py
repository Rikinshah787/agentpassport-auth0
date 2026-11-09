"""The Agent Passport panel: login, per-agent decisions and the tool API."""
import os
from collections import deque
from datetime import datetime
from urllib.parse import urlparse

import httpx
from auth0_api_python import ApiClient, ApiClientOptions
from auth0_server_python.auth_types import ConnectAccountOptions, LogoutOptions, StartInteractiveLoginOptions
from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for

from . import agents as passports
from .auth import auth0

app = Flask(__name__)
app.secret_key = os.getenv("AUTH0_SECRET")

# Checks a token the way a real API would: signature, issuer, audience and expiry.
verifier = ApiClient(
    ApiClientOptions(domain=os.getenv("AUTH0_DOMAIN"), audience=os.getenv("AUTH0_AUDIENCE"))
)

# The only scopes this app will ever ask Auth0 for.
GRANTABLE = ["github:read", "gmail:read"]

# What agents can do. Every row here makes a real call to a real app.
PASSPORT = {
    "GitHub": [("Read repos", "github:read")],
    "Gmail": [("Read inbox", "gmail:read")],
}

ACTIONS = {label: scope for rows in PASSPORT.values() for label, scope in rows}

# The MCP tools agents can call, and the passport row each one needs.
TOOLS = {
    "github_list_repos": "Read repos",
    "gmail_list_messages": "Read inbox",
}

# The apps connected through Token Vault. Each is one Auth0 connection.
SERVICES = {
    "github": {"label": "GitHub", "scopes": None},
    "gmail": {"label": "Gmail", "scopes": ["https://www.googleapis.com/auth/gmail.readonly"]},
}

ACTIVITY = deque(maxlen=6)
LAST_ERROR = {"text": None}
CONNECTED = {"checked": False, "services": set()}
GITHUB_API = "https://api.github.com"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"


def remember(where, error):
    # Keep the latest failure so the panel can show it, not just the terminal.
    extra = " ".join(str(getattr(error, name, "") or "") for name in ("title", "status", "validation_errors"))
    LAST_ERROR["text"] = f"{where}: {type(error).__name__}: {error} {extra}".strip()
    print(LAST_ERROR["text"])


def api_token(session):
    # The session can also hold a My Account token, so pick the one for our API.
    audience = os.getenv("AUTH0_AUDIENCE")
    return next((t for t in session["token_sets"] if t["audience"] == audience), session["token_sets"][0])


async def vault_token(service):
    # Auth0's Token Vault hands us the app's token. It never goes to the agent.
    token = await auth0.get_access_token_for_connection({"connection": service}, g.store_options)
    CONNECTED["services"].add(service)
    return token


async def refresh_connected():
    # Ask Auth0 once per login which apps are connected, so the panel shows the truth after a restart.
    if CONNECTED["checked"]:
        return
    CONNECTED["checked"] = True
    try:
        response = await auth0.list_connected_accounts(store_options=g.store_options)
        CONNECTED["services"] = {account.connection for account in response.accounts}
    except Exception as error:
        remember("Checking connected apps failed", error)


async def github_repos():
    token = await vault_token("github")
    response = httpx.get(
        f"{GITHUB_API}/user/repos",
        params={"sort": "updated", "per_page": 10},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=10,
    )
    response.raise_for_status()
    return {"repos": [repo["full_name"] for repo in response.json()]}


async def gmail_messages():
    # Sender and subject only, so message bodies stay out of the agent's reach.
    headers = {"Authorization": f"Bearer {await vault_token('gmail')}"}
    listing = httpx.get(f"{GMAIL_API}/messages", params={"maxResults": 5}, headers=headers, timeout=10)
    listing.raise_for_status()
    messages = []
    for item in listing.json().get("messages", []):
        detail = httpx.get(
            f"{GMAIL_API}/messages/{item['id']}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject"]},
            headers=headers,
            timeout=10,
        )
        detail.raise_for_status()
        found = {header["name"]: header["value"] for header in detail.json()["payload"]["headers"]}
        messages.append({"from": found.get("From"), "subject": found.get("Subject")})
    return {"messages": messages}


FETCH = {"github_list_repos": github_repos, "gmail_list_messages": gmail_messages}


async def check(session, agent, label):
    # Step 1: is this a genuine, unexpired Auth0 token for this API?
    try:
        claims = await verifier.verify_access_token(api_token(session)["access_token"])
    except Exception as error:
        remember("Token rejected", error)
        return 401, "Unauthorized: the token failed verification."

    # Step 2: is this agent allowed to do this?
    scope = ACTIONS[label]
    if scope not in passports.scopes()[agent]:
        return 403, f"Forbidden: {agent}'s passport doesn't include {scope}."
    if scope not in claims.get("scope", "").split():
        return 403, f"Forbidden: the Auth0 token doesn't include {scope}."
    return 200, f"OK: token verified, and {agent} may do {label}."


async def decide(session, agent, label):
    # The one place decisions are made, and the feed shows every one.
    code, message = await check(session, agent, label)
    ACTIVITY.appendleft({"time": datetime.now().strftime("%H:%M:%S"), "agent": agent, "action": label, "code": code})
    return code, message


def show_card(session, agent, result=None, code=200):
    user = None
    granted = []
    expires = None
    if session:
        token = api_token(session)
        user = session["user"]
        granted = token["scope"].split()
        expires = datetime.fromtimestamp(token["expires_at"]).strftime("%d %b %Y, %H:%M")

    # An agent can only use a scope that is on its passport AND in the Auth0 token.
    agents = passports.scopes()
    usable = [scope for scope in agents.get(agent, []) if scope in granted]

    card = {}
    for service, rows in PASSPORT.items():
        card[service] = [(label, scope, "allowed" if scope in usable else "blocked") for label, scope in rows]

    page = render_template(
        "index.html",
        card=card,
        user=user,
        granted=granted,
        grantable=GRANTABLE,
        expires=expires,
        issuer=os.getenv("AUTH0_DOMAIN"),
        audience=os.getenv("AUTH0_AUDIENCE"),
        agent=agent,
        agents=agents,
        running=passports.find(),
        services=SERVICES,
        connected=CONNECTED["services"] if session else set(),
        usable=usable,
        result=result,
        code=code,
        activity=ACTIVITY,
        last_error=LAST_ERROR["text"],
    )
    return page, code


@app.before_request
def before_request():
    g.store_options = {"request": request}
    # This panel changes local files, so only accept requests from itself.
    # A website open in your browser can't trigger these, even by renaming itself to localhost.
    origin = request.headers.get("Origin")
    if request.host.split(":")[0] not in ("localhost", "127.0.0.1"):
        abort(403)
    if request.method == "POST" and origin and urlparse(origin).netloc != request.host:
        abort(403)


@app.route("/")
async def index():
    agents = passports.scopes()
    agent = request.args.get("agent") or next(iter(agents), None)
    if agent is not None and agent not in agents:
        abort(404)
    session = await auth0.get_session(g.store_options)
    if session:
        await refresh_connected()
    return show_card(session, agent)


@app.route("/activity")
def activity():
    return render_template("activity.html", activity=ACTIVITY)


@app.route("/agents/issue", methods=["POST"])
async def issue_passports():
    session = await auth0.get_session(g.store_options)
    if not session:
        return redirect("/")
    issued, skipped = passports.issue()
    message = f"Passports issued to: {', '.join(issued) or 'no agents found'}. Restart each app to load its passport."
    if skipped:
        message += f" Skipped: {', '.join(skipped)}."
    return show_card(session, next(iter(passports.scopes()), None), message, 200)


@app.route("/agents/<agent>", methods=["POST"])
async def update_agent(agent):
    if agent not in passports.scopes():
        abort(404)
    if not await auth0.get_session(g.store_options):
        return redirect("/")
    passports.set_scopes(agent, [scope for scope in request.form.getlist("scope") if scope in GRANTABLE])
    return redirect(url_for("index", agent=agent))


@app.route("/agents/<agent>/remove", methods=["POST"])
async def remove_agent(agent):
    if not await auth0.get_session(g.store_options):
        return redirect("/")
    passports.remove(agent)
    return redirect("/")


@app.route("/api/tools/<tool>", methods=["POST"])
async def tool_call(tool):
    def reply(code, message, data=None):
        return jsonify(status=code, message=message, data=data), code

    # The key says which agent this is. A caller can't claim to be someone else.
    agent = passports.agent_for_key(request.headers.get("X-Passport-Key", ""))
    if not agent:
        return reply(401, "Unauthorized: this agent has no passport. Issue one from the panel.")
    if tool not in TOOLS:
        return reply(404, f"Unknown tool: {tool}")
    session = await auth0.get_session(g.store_options)
    if not session:
        return reply(401, "Unauthorized: nobody is logged in to the Agent Passport panel.")

    code, message = await decide(session, agent, TOOLS[tool])
    if code != 200:
        return reply(code, message)

    # Only an allowed call ever asks for an app token.
    try:
        return reply(200, message, await FETCH[tool]())
    except Exception as error:
        remember(f"{tool} failed", error)
        ACTIVITY[0]["code"] = 502
        return reply(502, "The passport allowed this, but the app call failed. Is that account connected on the panel?")


@app.route("/login")
async def login():
    # Login asks for scopes as /login?scope=github:read&scope=gmail:read
    wanted = request.args.getlist("scope")
    allowed = [scope for scope in wanted if scope in GRANTABLE]

    # offline_access gets a refresh token, which Token Vault needs.
    options = StartInteractiveLoginOptions(
        authorization_params={"scope": " ".join(["offline_access"] + allowed)}
    )
    url = await auth0.start_interactive_login(options, g.store_options)
    return redirect(url)


@app.route("/connect/<service>")
async def connect(service):
    if service not in SERVICES:
        abort(404)
    try:
        options = ConnectAccountOptions(connection=service, scopes=SERVICES[service]["scopes"])
        url = await auth0.start_connect_account(options, g.store_options)
    except Exception as error:
        remember(f"Connect {service} failed", error)
        return redirect("/?error=connect")
    return redirect(url)


@app.route("/callback")
async def callback():
    try:
        if "connect_code" in request.args:
            response = await auth0.complete_connect_account(str(request.url), g.store_options)
            CONNECTED["services"].add(response.connection)
            return redirect("/?connected=1")
        await auth0.complete_interactive_login(str(request.url), g.store_options)
        CONNECTED["checked"] = False
    except Exception as error:
        remember("Login failed", error)
        return redirect("/?error=login")
    return redirect("/")


@app.route("/logout")
async def logout():
    options = LogoutOptions(return_to=url_for("index", _external=True))
    url = await auth0.logout(options, g.store_options)
    return redirect(url)

