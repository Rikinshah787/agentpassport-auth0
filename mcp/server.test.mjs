import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

const passportFile = join(mkdtempSync(join(tmpdir(), "passport-")), "passports.json");
writeFileSync(passportFile, JSON.stringify({ "Test Agent": { key: "test-key", scopes: [] } }));

let panel;
let panelUrl;
let lastRequest;
let reply = { status: 200, message: "OK", data: { repos: ["a/b"] } };

before(async () => {
  panel = createServer((req, res) => {
    lastRequest = { url: req.url, key: req.headers["x-passport-key"] };
    req.resume();
    res.writeHead(reply.status, { "content-type": "application/json" });
    res.end(JSON.stringify(reply));
  });
  await new Promise((resolve) => panel.listen(0, "127.0.0.1", resolve));
  panelUrl = `http://127.0.0.1:${panel.address().port}`;
});

after(() => panel.close());

async function callTool(agent, url, tool = "github_list_repos") {
  const client = new Client({ name: "test", version: "1" });
  const env = { ...process.env, AGENT_NAME: agent, PASSPORT_URL: url, PASSPORT_FILE: passportFile };
  await client.connect(new StdioClientTransport({ command: "node", args: ["server.mjs"], env, stderr: "ignore" }));
  try {
    const { tools } = await client.listTools();
    const result = await client.callTool({ name: tool, arguments: {} });
    return { tools: tools.map((item) => item.name), error: Boolean(result.isError), text: result.content[0].text };
  } finally {
    await client.close();
  }
}

test("lists only the real tools", async () => {
  const { tools } = await callTool("Test Agent", panelUrl);
  assert.deepEqual(tools.sort(), ["github_list_repos", "gmail_list_messages"]);
});

test("sends this agent's own key and returns the panel's answer", async () => {
  reply = { status: 200, message: "OK", data: { repos: ["a/b"] } };
  const { error, text } = await callTool("Test Agent", panelUrl);
  assert.equal(lastRequest.key, "test-key");
  assert.equal(lastRequest.url, "/api/tools/github_list_repos");
  assert.equal(error, false);
  assert.match(text, /^200: OK/);
  assert.match(text, /a\/b/);
});

test("a refusal is reported as an error", async () => {
  reply = { status: 403, message: "Forbidden", data: null };
  const { error, text } = await callTool("Test Agent", panelUrl);
  assert.equal(error, true);
  assert.match(text, /^403: Forbidden/);
});

test("an agent with no passport never calls the panel", async () => {
  lastRequest = undefined;
  const { error, text } = await callTool("Unknown Agent", panelUrl);
  assert.equal(error, true);
  assert.match(text, /has no passport/);
  assert.equal(lastRequest, undefined);
});

test("fails closed when the panel is down", async () => {
  const { error, text } = await callTool("Test Agent", "http://127.0.0.1:9");
  assert.equal(error, true);
  assert.match(text, /not performed/);
});
