export const TOOL_NAMES = [
  "cesta_ingest",
  "cesta_query",
  "cesta_review",
  "cesta_correct",
  "cesta_receipt",
  "cesta_dashboard_status",
] as const;

export type CestaToolName = (typeof TOOL_NAMES)[number];

export type PluginConfig = {
  socketPath: string;
  inboundMediaRoot: string;
  agentId: "cesta";
  groupJid: string;
  allowedRequesterIds: string[];
};

export type ToolFactoryContext = {
  agentId?: string;
  messageChannel?: string;
  sessionKey?: string;
  requesterSenderId?: string;
  senderIsOwner?: boolean;
  sandboxed?: boolean;
  deliveryContext?: { to?: string };
};

export type TrustedBridgeContext = {
  agent_id: string;
  channel: "whatsapp";
  group_jid: string;
  session_key: string;
  requester_sender_id: string;
  sender_is_owner: boolean;
  received_at: string;
};

export type BridgeRequest = {
  operation:
    | "stage_inbound"
    | "ingest"
    | "query"
    | "review"
    | "correct"
    | "receipt"
    | "dashboard_status";
  context: TrustedBridgeContext;
  params: Record<string, unknown>;
};

export type AgentTool = {
  name: CestaToolName;
  label: string;
  description: string;
  parameters: TSchema;
  execute: (toolCallId: string, params: Record<string, unknown>) => Promise<AgentToolResultLike>;
};

export type AgentToolResultLike = {
  content: Array<{ type: "text"; text: string }>;
  details: unknown;
};
import type { TSchema } from "typebox";
