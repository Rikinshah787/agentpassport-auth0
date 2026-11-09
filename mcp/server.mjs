// Agent Passport MCP server: forwards every tool call to the panel and holds no logic.
// stdout carries the MCP protocol, so anything logged goes to stderr.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { readFileSync } from "node:fs";

const {
  AGENT_NAME = "Claude Code",
  PASSPORT_URL = "http://localhost:5000",
  PASSPORT_FILE = new URL("../passports.json", import.meta.url),
} = process.env;

// Read only this agent's key, on each call, so a newly issued passport works without a restart.
// This process never reads .env, so the Auth0 secrets stay out of it.
function passportKey() {
  try {
    return JSON.parse(readFileSync(PASSPORT_FILE, "utf8"))[AGENT_NAME]?.key;
  } catch (err) {
    console.error("Agent Passport: could not read passports:", err.code ?? err.message);
    return undefined;
  }
}

const failure = (text) => ({ content: [{ type: "text", text }], isError: true });

async function forward(tool, args) {
  const key = passportKey();
  if (!key) return failure(`${AGENT_NAME} has no passport, so this action was not performed. Issue one from the Agent Passport panel.`);
  try {
    const res = await fetch(new URL(`/api/tools/${tool}`, PASSPORT_URL), {
      method: "POST",
      headers: { "content-type": "application/json", "X-Passport-Key": key },
      body: JSON.stringify({ args }),
      redirect: "error", // a redirect must never carry the key somewhere else
      signal: AbortSignal.timeout(30_000),
    });
    const { status, message, data } = await res.json();
    if (status !== res.status || typeof message !== "string") throw new Error(`bad panel response, HTTP ${res.status}`);
    const details = data === null || data === undefined ? "" : `\n${JSON.stringify(data, null, 2)}`;
    return { content: [{ type: "text", text: `${status}: ${message}${details}` }], isError: status >= 400 };
  } catch (err) {
    console.error("Agent Passport: failing closed:", err.message);
    return failure("Agent Passport panel isn't reachable, so this action was not performed.");
  }
}

const tools = {
  github_list_repos: "List GitHub repositories. Access is checked by Agent Passport.",
  gmail_list_messages: "List the sender and subject of recent emails. Access is checked by Agent Passport.",
};

const server = new McpServer({ name: "agent-passport", version: "1.0.0" });
for (const [name, description] of Object.entries(tools)) {
  server.registerTool(name, { description, inputSchema: {} }, (args) => forward(name, args));
}
await server.connect(new StdioServerTransport());
