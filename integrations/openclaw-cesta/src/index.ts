import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { createBridgeClient, type BridgeClient } from "./client.js";
import { hasInboundMedia, inboundMessageKey, stageInboundMedia } from "./inbound.js";
import { trustedContext } from "./security.js";
import { createCestaTools } from "./tools.js";
import type { PluginConfig, TrustedBridgeContext } from "./types.js";

const configSchema = Type.Object(
  {
    socketPath: Type.String({ minLength: 1, pattern: "^/" }),
    inboundMediaRoot: Type.String({ minLength: 1, pattern: "^/" }),
    agentId: Type.Literal("cesta"),
    groupJid: Type.String({ pattern: "@g\\.us$" }),
    allowedRequesterIds: Type.Array(Type.String({ minLength: 1 }), { minItems: 2, uniqueItems: true }),
  },
  { additionalProperties: false },
);

const metadataContext: TrustedBridgeContext = {
  agent_id: "cesta",
  channel: "whatsapp",
  group_jid: "metadata@g.us",
  session_key: "agent:cesta:whatsapp:group:metadata@g.us",
  requester_sender_id: "metadata",
  sender_is_owner: false,
  received_at: "1970-01-01T00:00:00.000Z",
};
const metadataClient: BridgeClient = async () => {
  throw new Error("metadata-only tool must never execute");
};

const clients = new Map<string, BridgeClient>();
const inboundBatches = new Map<string, { batchId: string; expiresAt: number }>();
const inboundGenerations = new Map<string, object>();
const INBOUND_BATCH_TTL_MS = 30 * 60_000;

function currentInboundBatchId(key: string): string | undefined {
  const entry = inboundBatches.get(key);
  if (!entry) return undefined;
  if (entry.expiresAt < Date.now()) {
    inboundBatches.delete(key);
    return undefined;
  }
  return entry.batchId;
}

function clientFor(socketPath: string): BridgeClient {
  const existing = clients.get(socketPath);
  if (existing) return existing;
  const client = createBridgeClient(socketPath);
  clients.set(socketPath, client);
  return client;
}

const plugin = defineToolPlugin({
  id: "cesta-local",
  name: "Cesta Local",
  description: "Narrow trusted Gateway bridge for Cesta Inteligente",
  configSchema,
  tools: (tool) =>
    createCestaTools(metadataContext, metadataClient).map((definition) =>
      tool({
        name: definition.name,
        label: definition.label,
        description: definition.description,
        parameters: definition.parameters,
        optional: true,
        factory({ api, toolContext }) {
          const config = api.pluginConfig as PluginConfig;
          const context = trustedContext(config, toolContext);
          if (!context) return null;
          const key = `${context.session_key}\u0000${context.requester_sender_id}`;
          const runtimeTool = createCestaTools(
            context,
            clientFor(config.socketPath),
            () => currentInboundBatchId(key),
          ).find(
            (candidate) => candidate.name === definition.name,
          );
          return runtimeTool ?? null;
        },
      }),
    ),
});

const registerTools = plugin.register;
plugin.register = (api) => {
  registerTools(api);
  api.on("message_received", async (event, hookContext) => {
    const config = api.pluginConfig as PluginConfig;
    const key = inboundMessageKey(config, event, hookContext);
    if (!key) return;
    // Text-only follow-ups such as "ya está" must keep the batch correlation
    // created by the preceding media event. Only a new media event supersedes it.
    if (!hasInboundMedia(event)) return;
    const generation = {};
    inboundGenerations.set(key, generation);
    inboundBatches.delete(key);
    try {
      const result = await stageInboundMedia(config, event, hookContext, clientFor(config.socketPath));
      if (inboundGenerations.get(key) !== generation || !result || typeof result !== "object") return;
      const batchId = (result as { batch_id?: unknown }).batch_id;
      if (typeof batchId === "string" && /^batch_[a-f0-9]{32}$/.test(batchId)) {
        inboundBatches.set(key, { batchId, expiresAt: Date.now() + INBOUND_BATCH_TTL_MS });
      }
    } catch {
      // Fail closed without logging receipt paths, OCR or authenticated identities.
      api.logger.warn?.("cesta-local: inbound media staging rejected");
    }
  });
};

export default plugin;
