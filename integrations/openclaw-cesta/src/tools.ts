import { Type } from "typebox";
import type { BridgeClient } from "./client.js";
import type { AgentTool, AgentToolResultLike, BridgeRequest, TrustedBridgeContext } from "./types.js";

function toolResult(result: unknown): AgentToolResultLike {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify({ source: "cesta_sqlite", untrusted_receipt_content: true, result }),
      },
    ],
    details: result,
  };
}

function execute(
  client: BridgeClient,
  context: TrustedBridgeContext,
  operation: BridgeRequest["operation"],
  params: Record<string, unknown>,
) {
  return client({ operation, context, params }).then(toolResult);
}

const FilterValue = Type.Union([Type.String({ maxLength: 160 }), Type.Number(), Type.Boolean()]);

export function createCestaTools(
  context: TrustedBridgeContext,
  client: BridgeClient,
  currentInboundBatchId: () => string | undefined = () => undefined,
): AgentTool[] {
  return [
    {
      name: "cesta_ingest",
      label: "Cesta: ingestión",
      description: "Añade IDs opacos de medios al lote actual, consulta su estado o lo finaliza.",
      parameters: Type.Object(
        {
          action: Type.Union([Type.Literal("append"), Type.Literal("finalize"), Type.Literal("status")]),
          mediaIds: Type.Optional(
            Type.Array(Type.String({ pattern: "^sha256:[a-f0-9]{64}$" }), { maxItems: 12, uniqueItems: true }),
          ),
        },
        { additionalProperties: false },
      ),
      execute: (_id, params) => {
        const batchId = params.action === "status" || params.action === "finalize" ? currentInboundBatchId() : undefined;
        return execute(client, context, "ingest", {
          action: params.action,
          media_ids: params.mediaIds ?? [],
          ...(batchId ? { batch_id: batchId } : {}),
        });
      },
    },
    {
      name: "cesta_query",
      label: "Cesta: consulta",
      description: "Ejecuta una consulta tipada y parametrizada exclusivamente contra SQLite.",
      parameters: Type.Object(
        {
          intent: Type.Union([
            Type.Literal("month_total"),
            Type.Literal("supermarket_total"),
            Type.Literal("product_quantity"),
            Type.Literal("price_history"),
            Type.Literal("comparable_price_by_store"),
            Type.Literal("comparable_store_summary"),
            Type.Literal("discounts"),
            Type.Literal("receipt_history"),
          ]),
          filters: Type.Optional(Type.Record(Type.String({ maxLength: 40 }), FilterValue)),
        },
        { additionalProperties: false },
      ),
      execute: (_id, params) =>
        execute(client, context, "query", {
          intent: params.intent,
          filters: params.filters ?? {},
        }),
    },
    {
      name: "cesta_review",
      label: "Cesta: revisión",
      description: "Obtiene o resuelve la revisión concreta activa del grupo.",
      parameters: Type.Object(
        { answer: Type.Optional(Type.String({ minLength: 1, maxLength: 120 })) },
        { additionalProperties: false },
      ),
      execute: (_id, params) => execute(client, context, "review", { answer: params.answer }),
    },
    {
      name: "cesta_correct",
      label: "Cesta: corrección",
      description: "Corrige un campo permitido de un ticket y vuelve a validarlo.",
      parameters: Type.Object(
        {
          receiptId: Type.Integer({ minimum: 1 }),
          field: Type.Literal("purchase_date"),
          value: Type.String({ pattern: "^\\d{4}-\\d{2}-\\d{2}$" }),
        },
        { additionalProperties: false },
      ),
      execute: (_id, params) =>
        execute(client, context, "correct", {
          receipt_id: params.receiptId,
          field: params.field,
          value: params.value,
        }),
    },
    {
      name: "cesta_receipt",
      label: "Cesta: ticket",
      description: "Recupera desde SQLite el detalle auditable de un ticket concreto.",
      parameters: Type.Object({ receiptId: Type.Integer({ minimum: 1 }) }, { additionalProperties: false }),
      execute: (_id, params) => execute(client, context, "receipt", { receipt_id: params.receiptId }),
    },
    {
      name: "cesta_dashboard_status",
      label: "Cesta: dashboard",
      description: "Comprueba si el dashboard protegido está disponible para el usuario funcional.",
      parameters: Type.Object({}, { additionalProperties: false }),
      execute: () => execute(client, context, "dashboard_status", {}),
    },
  ];
}
