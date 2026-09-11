import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createBridgeClient } from "../src/client.js";
import type { BridgeRequest } from "../src/types.js";

test("client uses a Unix socket and bounded NDJSON protocol", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "cesta-plugin-"));
  const socketPath = path.join(root, "bridge.sock");
  const received: BridgeRequest[] = [];
  const server = net.createServer((socket) => {
    socket.setEncoding("utf8");
    socket.once("data", (data: string) => {
      received.push(JSON.parse(data.trim()));
      socket.end(`${JSON.stringify({ ok: true, result: { status: "local_only" } })}\n`);
    });
  });
  await new Promise<void>((resolve) => server.listen(socketPath, resolve));
  const client = createBridgeClient(socketPath, 2_000);
  const request: BridgeRequest = {
    operation: "dashboard_status",
    context: {
      agent_id: "cesta",
      channel: "whatsapp",
      group_jid: "group@g.us",
      session_key: "agent:cesta:whatsapp:group:group@g.us",
      requester_sender_id: "alex@jid",
      sender_is_owner: true,
      received_at: "2026-08-20T18:00:00.000Z",
    },
    params: {},
  };
  const result = await client(request);
  assert.deepEqual(result, { status: "local_only" });
  assert.deepEqual(received, [request]);
  await new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve())));
});

test("compiled privileged plugin contains no process execution API", async () => {
  const files = ["client.js", "inbound.js", "index.js", "security.js", "tools.js"];
  const source = (
    await Promise.all(files.map((file) => readFile(new URL(`../src/${file}`, import.meta.url), "utf8")))
  ).join("\n");
  for (const forbidden of ["node:child_process", "exec(", "execFile(", "spawn(", "fork("]) {
    assert.equal(source.includes(forbidden), false, `forbidden privileged API found: ${forbidden}`);
  }
});
