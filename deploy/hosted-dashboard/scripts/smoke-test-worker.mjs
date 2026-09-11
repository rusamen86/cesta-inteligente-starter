import assert from "node:assert/strict";
import worker from "../worker/cesta-dashboard-worker.js";

let stored = null;
const DB = {
  async exec() {},
  prepare(sql) {
    return {
      bind(...values) {
        return { async run() { stored = values[2]; return { success: true }; } };
      },
      async first() { return stored ? { payload_json: stored } : null; }
    };
  }
};
const env = { DB, SYNC_TOKEN: "test-token" };
const snapshot = {
  schemaVersion: 1,
  generatedAt: "2026-09-11T07:30:00+02:00",
  receipts: [{ id: 1, date: "2026-09-10", supermarket: "Lupa", store: "", articleCount: 9, amountPaidCents: 3007, savingsCents: 0, validationStatus: "valid" }],
  items: [{ id: 1, receiptId: 1, productStableId: "sku-1", product: "Leche", comparableProductStableId: "cp-1", comparableProduct: "Leche", family: "Lácteos", category: "Alimentación", date: "2026-09-10", supermarket: "Lupa", quantity: 1, normalizedQuantity: 1, normalizedUnit: "L", lineFinalCents: 120, normalizedPriceCents: 120 }]
};

const unauthorized = await worker.fetch(new Request("https://example.test/api/sync", { method: "POST", body: JSON.stringify(snapshot) }), env);
assert.equal(unauthorized.status, 401);

const synced = await worker.fetch(new Request("https://example.test/api/sync", { method: "POST", headers: { Authorization: "Bearer test-token", "Content-Type": "application/json" }, body: JSON.stringify(snapshot) }), env);
assert.equal(synced.status, 200);
assert.equal((await synced.json()).items, 1);

const dashboard = await worker.fetch(new Request("https://example.test/api/dashboard"), env);
assert.equal(dashboard.status, 200);
assert.equal((await dashboard.json()).receipts.length, 1);

const home = await worker.fetch(new Request("https://example.test/"), env);
assert.equal(home.status, 200);
assert.match(await home.text(), /Cesta Inteligente/);

console.log("worker smoke test: ok");

