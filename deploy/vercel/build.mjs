import { mkdir, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

import worker from "../hosted-dashboard/worker/cesta-dashboard-worker.js";

const root = resolve(import.meta.dirname, "../..");
const dist = resolve(root, "dist");

await rm(dist, { recursive: true, force: true });
await mkdir(dist, { recursive: true });

const page = await worker.fetch(new Request("https://cesta.example/"), {});
if (!page.ok) throw new Error(`No se pudo generar el dashboard: ${page.status}`);

const html = (await page.text()).replace(
  "fetch('/api/dashboard',{cache:'no-store'})",
  "fetch('/snapshot.json',{cache:'no-store'})",
);

await Promise.all([
  writeFile(resolve(dist, "index.html"), html, "utf8"),
  writeFile(resolve(dist, "404.html"), html, "utf8"),
  writeFile(resolve(dist, "snapshot.json"), JSON.stringify(demoSnapshot()), "utf8"),
  writeWorkerAsset("/manifest.webmanifest", "manifest.webmanifest"),
  writeWorkerAsset("/app-icon.svg", "app-icon.svg"),
]);

console.log("Vercel static demo generated in dist/");

async function writeWorkerAsset(pathname, filename) {
  const response = await worker.fetch(new Request(`https://cesta.example${pathname}`), {});
  if (!response.ok) throw new Error(`No se pudo generar ${filename}: ${response.status}`);
  await writeFile(resolve(dist, filename), await response.text(), "utf8");
}

function demoSnapshot() {
  const now = new Date();
  const isoDate = (daysAgo) => {
    const value = new Date(now);
    value.setUTCDate(value.getUTCDate() - daysAgo);
    return value.toISOString().slice(0, 10);
  };

  const receipts = [
    { id: 1, date: isoDate(24), supermarket: "Mercado Norte", store: "Centro", articleCount: 8, amountPaidCents: 2840, savingsCents: 210, validationStatus: "valid" },
    { id: 2, date: isoDate(15), supermarket: "Super Ahorro", store: "Barrio", articleCount: 12, amountPaidCents: 4315, savingsCents: 380, validationStatus: "valid" },
    { id: 3, date: isoDate(7), supermarket: "Mercado Norte", store: "Centro", articleCount: 6, amountPaidCents: 2260, savingsCents: 90, validationStatus: "valid" },
    { id: 4, date: isoDate(2), supermarket: "La Despensa", store: "Plaza", articleCount: 10, amountPaidCents: 3675, savingsCents: 250, validationStatus: "valid" },
  ];

  const products = [
    [1, 1, "Leche", "Lácteos", "Alimentación", 360, 120],
    [2, 1, "Plátano", "Fruta", "Alimentación", 285, 190],
    [3, 1, "Pan integral", "Pan", "Alimentación", 220, 220],
    [4, 2, "Leche", "Lácteos", "Alimentación", 390, 130],
    [5, 2, "Tomate", "Verdura", "Alimentación", 340, 227],
    [6, 2, "Huevos", "Huevos", "Alimentación", 310, 26],
    [7, 3, "Yogur natural", "Lácteos", "Alimentación", 260, 65],
    [8, 3, "Plátano", "Fruta", "Alimentación", 300, 200],
    [9, 4, "Leche", "Lácteos", "Alimentación", 375, 125],
    [10, 4, "Tomate", "Verdura", "Alimentación", 360, 240],
    [11, 4, "Papel de cocina", "Papel doméstico", "Limpieza y hogar", 295, 295],
  ];

  const receiptById = new Map(receipts.map((receipt) => [receipt.id, receipt]));
  const items = products.map(([id, receiptId, product, family, category, lineFinalCents, normalizedPriceCents]) => ({
    id,
    receiptId,
    productStableId: `sku-${id}`,
    product,
    comparableProductStableId: `cp-${product.toLowerCase().replaceAll(" ", "-")}`,
    comparableProduct: product,
    family,
    category,
    date: receiptById.get(receiptId).date,
    supermarket: receiptById.get(receiptId).supermarket,
    quantity: 1,
    normalizedQuantity: 1,
    normalizedUnit: product === "Huevos" ? "egg" : product === "Leche" ? "L" : "kg",
    lineFinalCents,
    normalizedPriceCents,
  }));

  return { schemaVersion: 1, generatedAt: now.toISOString(), receipts, items };
}
