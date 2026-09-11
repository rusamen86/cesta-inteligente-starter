import path from "node:path";
import { lstat, realpath } from "node:fs/promises";
import type { BridgeClient } from "./client.js";
import type { PluginConfig, TrustedBridgeContext } from "./types.js";

export type InboundMessageEvent = {
  timestamp?: number;
  senderId?: string;
  sessionKey?: string;
  media?: Array<{
    path?: string;
    url?: string;
    contentType?: string;
    kind?: string;
    messageId?: string;
    workspaceDir?: string;
  }>;
  mediaStagingPending?: boolean;
  metadata?: Record<string, unknown>;
};

export type InboundMessageContext = {
  channelId?: string;
  conversationId?: string;
  sessionKey?: string;
  senderId?: string;
};

export function inboundMessageKey(
  config: PluginConfig,
  event: InboundMessageEvent,
  hookContext: InboundMessageContext,
): string | null {
  const sessionKey = event.sessionKey ?? hookContext.sessionKey;
  const expectedSession = `agent:${config.agentId}:whatsapp:group:${config.groupJid}`;
  const senderId = event.senderId ?? hookContext.senderId;
  const metadata = event.metadata ?? {};
  const target =
    typeof metadata.originatingTo === "string"
      ? metadata.originatingTo
      : hookContext.conversationId ?? (typeof metadata.to === "string" ? metadata.to : undefined);

  if (hookContext.channelId !== "whatsapp") return null;
  if (sessionKey !== expectedSession) return null;
  if (target !== config.groupJid) return null;
  if (!senderId || !config.allowedRequesterIds.includes(senderId)) return null;
  return `${expectedSession}\u0000${senderId}`;
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.length > 0);
}

export function hasInboundMedia(event: InboundMessageEvent): boolean {
  if (event.mediaStagingPending === true) return false;
  const metadata = event.metadata ?? {};
  if ((event.media ?? []).some((fact) => typeof fact.path === "string" && fact.path.length > 0)) return true;
  if (strings(metadata.mediaPaths).length > 0) return true;
  return typeof metadata.mediaPath === "string" && metadata.mediaPath.length > 0;
}

function receivedAt(timestamp: number | undefined): string {
  if (!Number.isFinite(timestamp)) return new Date().toISOString();
  const value = timestamp as number;
  const milliseconds = value < 1_000_000_000_000 ? value * 1_000 : value;
  return new Date(milliseconds).toISOString();
}

export async function stageInboundMedia(
  config: PluginConfig,
  event: InboundMessageEvent,
  hookContext: InboundMessageContext,
  client: BridgeClient,
): Promise<unknown | null> {
  const sessionKey = event.sessionKey ?? hookContext.sessionKey;
  const expectedSession = `agent:${config.agentId}:whatsapp:group:${config.groupJid}`;
  const senderId = event.senderId ?? hookContext.senderId;
  const metadata = event.metadata ?? {};
  if (!inboundMessageKey(config, event, hookContext) || !senderId) return null;

  if (!hasInboundMedia(event)) return null;
  const canonicalPaths = (event.media ?? [])
    .map((fact) => fact.path)
    .filter((mediaPath): mediaPath is string => typeof mediaPath === "string" && mediaPath.length > 0);
  // OpenClaw 2026.8 exposes ordered media facts on the event. Keep the
  // metadata projection only as a compatibility fallback for older runtimes.
  const rawPaths = canonicalPaths.length > 0 ? canonicalPaths : [...strings(metadata.mediaPaths)];
  if (canonicalPaths.length === 0 && typeof metadata.mediaPath === "string" && metadata.mediaPath.length > 0) {
    rawPaths.push(metadata.mediaPath);
  }
  const mediaPaths = [...new Set(rawPaths)];
  if (mediaPaths.length === 0) return null;
  if (mediaPaths.length > 12) throw new Error("Cesta accepts at most 12 media files per message");

  const configuredRoot = path.resolve(config.inboundMediaRoot);
  const realRoot = await realpath(configuredRoot);
  const mediaRefs: string[] = [];
  for (const mediaPath of mediaPaths) {
    const resolved = path.resolve(mediaPath);
    const relative = path.relative(configuredRoot, resolved);
    if (!relative || path.isAbsolute(relative) || relative === ".." || relative.startsWith(`..${path.sep}`)) {
      throw new Error("Inbound media escaped the configured root");
    }
    let cursor = configuredRoot;
    for (const part of relative.split(path.sep)) {
      cursor = path.join(cursor, part);
      const info = await lstat(cursor);
      if (info.isSymbolicLink()) throw new Error("Inbound media symlinks are not allowed");
    }
    const realMediaPath = await realpath(resolved);
    const realRelative = path.relative(realRoot, realMediaPath);
    if (
      !realRelative ||
      path.isAbsolute(realRelative) ||
      realRelative === ".." ||
      realRelative.startsWith(`..${path.sep}`)
    ) {
      throw new Error("Inbound media real path escaped the configured root");
    }
    mediaRefs.push(relative.split(path.sep).join("/"));
  }

  const context: TrustedBridgeContext = {
    agent_id: config.agentId,
    channel: "whatsapp",
    group_jid: config.groupJid,
    session_key: expectedSession,
    requester_sender_id: senderId,
    // Functional ingestion never relies on an owner bit. The DB is authoritative.
    sender_is_owner: false,
    received_at: receivedAt(event.timestamp),
  };
  return client({ operation: "stage_inbound", context, params: { media_refs: mediaRefs } });
}
