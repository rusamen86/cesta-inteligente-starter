import type { PluginConfig, ToolFactoryContext, TrustedBridgeContext } from "./types.js";

export function trustedContext(
  config: PluginConfig,
  context: ToolFactoryContext,
  now: Date = new Date(),
): TrustedBridgeContext | null {
  if (context.agentId !== config.agentId) return null;
  if (context.messageChannel !== "whatsapp") return null;
  if (context.sandboxed !== true) return null;
  if (!context.requesterSenderId || !config.allowedRequesterIds.includes(context.requesterSenderId)) return null;
  const expectedSession = `agent:${config.agentId}:whatsapp:group:${config.groupJid}`;
  if (context.sessionKey !== expectedSession) return null;
  if (context.deliveryContext?.to && context.deliveryContext.to !== config.groupJid) return null;
  return {
    agent_id: config.agentId,
    channel: "whatsapp",
    group_jid: config.groupJid,
    session_key: expectedSession,
    requester_sender_id: context.requesterSenderId,
    sender_is_owner: context.senderIsOwner === true,
    received_at: now.toISOString(),
  };
}
