import assert from "node:assert/strict";
import test from "node:test";
import { trustedContext } from "../src/security.js";
import type { PluginConfig, ToolFactoryContext } from "../src/types.js";

const config: PluginConfig = {
  socketPath: "/tmp/cesta-test.sock",
  inboundMediaRoot: "/tmp/openclaw-inbound",
  agentId: "cesta",
  groupJid: "120363000000000000@g.us",
  allowedRequesterIds: ["alex@jid", "sam@lid"],
};

const valid: ToolFactoryContext = {
  agentId: "cesta",
  messageChannel: "whatsapp",
  sessionKey: "agent:cesta:whatsapp:group:120363000000000000@g.us",
  requesterSenderId: "alex@jid",
  senderIsOwner: true,
  sandboxed: true,
  deliveryContext: { to: "120363000000000000@g.us" },
};

test("trusted context is derived only from runtime context", () => {
  const result = trustedContext(config, valid, new Date("2026-08-20T18:00:00Z"));
  assert.deepEqual(result, {
    agent_id: "cesta",
    channel: "whatsapp",
    group_jid: config.groupJid,
    session_key: valid.sessionKey,
    requester_sender_id: "alex@jid",
    sender_is_owner: true,
    received_at: "2026-08-20T18:00:00.000Z",
  });
});

for (const [label, mutation] of [
  ["wrong agent", { agentId: "main" }],
  ["wrong channel", { messageChannel: "telegram" }],
  ["not sandboxed", { sandboxed: false }],
  ["missing sandbox bit", { sandboxed: undefined }],
  ["wrong group session", { sessionKey: "agent:cesta:whatsapp:group:other@g.us" }],
  ["wrong delivery target", { deliveryContext: { to: "other@g.us" } }],
  ["display-name impersonation", { requesterSenderId: "Alex" }],
  ["unknown sender", { requesterSenderId: "attacker@jid" }],
] as const) {
  test(`fails closed for ${label}`, () => {
    assert.equal(trustedContext(config, { ...valid, ...mutation }), null);
  });
}
