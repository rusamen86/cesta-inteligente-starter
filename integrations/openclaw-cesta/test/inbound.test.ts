import assert from "node:assert/strict";
import { mkdtemp, mkdir, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import plugin from "../src/index.js";
import { hasInboundMedia, stageInboundMedia } from "../src/inbound.js";
import type { BridgeRequest, PluginConfig } from "../src/types.js";

async function fixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "cesta-inbound-"));
  await mkdir(path.join(root, "wa"));
  const mediaPath = path.join(root, "wa", "ticket.jpg");
  await writeFile(mediaPath, "ticket");
  const config: PluginConfig = {
    socketPath: "/tmp/cesta-test.sock",
    inboundMediaRoot: root,
    agentId: "cesta",
    groupJid: "120363000000000000@g.us",
    allowedRequesterIds: ["alex@jid", "sam@lid"],
  };
  const event = {
    timestamp: 1_787_251_200,
    senderId: "alex@jid",
    sessionKey: `agent:cesta:whatsapp:group:${config.groupJid}`,
    media: [{ path: mediaPath, contentType: "image/jpeg", messageId: "wamid-ticket" }],
    metadata: {
      // OpenClaw maps WhatsApp group ingress this way: `to` is the connected
      // account and `originatingTo` is the authenticated group route.
      to: "+34000000002",
      originatingTo: config.groupJid,
    },
  };
  const hookContext = {
    channelId: "whatsapp",
    conversationId: config.groupJid,
    sessionKey: event.sessionKey,
    senderId: event.senderId,
  };
  return { root, mediaPath, config, event, hookContext };
}

test("trusted inbound hook converts an allowed path to a relative bridge reference", async () => {
  const { config, event, hookContext } = await fixture();
  const requests: BridgeRequest[] = [];
  const result = await stageInboundMedia(config, event, hookContext, async (request) => {
    requests.push(request);
    return { status: "open" };
  });
  assert.deepEqual(result, { status: "open" });
  assert.equal(requests.length, 1);
  assert.equal(requests[0].operation, "stage_inbound");
  assert.deepEqual(requests[0].params, { media_refs: ["wa/ticket.jpg"] });
  assert.equal(requests[0].context.requester_sender_id, "alex@jid");
  assert.equal(requests[0].context.sender_is_owner, false);
});

test("trusted inbound hook prefers canonical event media over stale legacy metadata", async () => {
  const { config, event, hookContext, mediaPath } = await fixture();
  const requests: BridgeRequest[] = [];
  await stageInboundMedia(
    config,
    {
      ...event,
      metadata: {
        ...event.metadata,
        mediaPaths: [path.join(config.inboundMediaRoot, "old-ticket.jpg")],
      },
    },
    hookContext,
    async (request) => {
      requests.push(request);
      return { status: "open" };
    },
  );
  assert.deepEqual(requests[0].params, {
    media_refs: [path.relative(config.inboundMediaRoot, mediaPath).split(path.sep).join("/")],
  });
});

test("trusted inbound hook waits while canonical media staging is pending", async () => {
  const { config, event, hookContext } = await fixture();
  let called = false;
  const result = await stageInboundMedia(
    config,
    { ...event, media: undefined, mediaStagingPending: true },
    hookContext,
    async () => {
      called = true;
      return {};
    },
  );
  assert.equal(result, null);
  assert.equal(called, false);
});

test("text-only follow-ups are not treated as new media events", async () => {
  assert.equal(hasInboundMedia({ metadata: {} }), false);
  assert.equal(hasInboundMedia({ mediaStagingPending: true, media: [{ path: "/pending/ticket.jpg" }] }), false);
  assert.equal(hasInboundMedia({ media: [{ path: "/inbound/ticket.jpg" }] }), true);
});

test("trusted inbound hook ignores any unauthorised route or identity", async () => {
  const { config, event, hookContext } = await fixture();
  const client = async () => {
    throw new Error("must not call bridge");
  };
  assert.equal(await stageInboundMedia(config, { ...event, senderId: "Alex" }, hookContext, client), null);
  assert.equal(await stageInboundMedia(config, event, { ...hookContext, channelId: "telegram" }, client), null);
  assert.equal(
    await stageInboundMedia(config, { ...event, sessionKey: "agent:main:main" }, hookContext, client),
    null,
  );
});

test("trusted inbound hook rejects outside paths and symlinks", async () => {
  const { root, config, event, hookContext } = await fixture();
  const outside = path.join(path.dirname(root), "outside-ticket.jpg");
  await writeFile(outside, "outside");
  await assert.rejects(
    stageInboundMedia(
      config,
      { ...event, media: [{ path: outside, contentType: "image/jpeg" }] },
      hookContext,
      async () => ({}),
    ),
  );

  const link = path.join(root, "wa", "link.jpg");
  await symlink(outside, link);
  await assert.rejects(
    stageInboundMedia(
      config,
      { ...event, media: [{ path: link, contentType: "image/jpeg" }] },
      hookContext,
      async () => ({}),
    ),
  );
});

test("plugin entry registers the trusted inbound hook alongside only six tools", () => {
  const hooks = new Map<string, unknown>();
  const registeredTools: unknown[] = [];
  const api = {
    pluginConfig: {
      socketPath: "/tmp/cesta-test.sock",
      inboundMediaRoot: "/tmp/openclaw-inbound",
      agentId: "cesta",
      groupJid: "120363000000000000@g.us",
      allowedRequesterIds: ["alex@jid", "sam@lid"],
    },
    registerTool(tool: unknown) {
      registeredTools.push(tool);
    },
    on(name: string, handler: unknown) {
      hooks.set(name, handler);
    },
    logger: { warn() {} },
  };
  plugin.register(api as never);
  assert.equal(registeredTools.length, 6);
  assert.equal(typeof hooks.get("message_received"), "function");
  assert.deepEqual([...hooks.keys()], ["message_received"]);
});
