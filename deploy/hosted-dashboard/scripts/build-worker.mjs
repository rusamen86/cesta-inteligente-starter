import { cp, mkdir, readFile, rm } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const dist = resolve(root, "dist");

await rm(dist, { recursive: true, force: true });
await mkdir(resolve(dist, "server"), { recursive: true });
await mkdir(resolve(dist, ".openai"), { recursive: true });
await cp(resolve(root, "worker/cesta-dashboard-worker.js"), resolve(dist, "server/index.js"));
await cp(resolve(root, ".openai/hosting.json"), resolve(dist, ".openai/hosting.json"));

const hosting = JSON.parse(await readFile(resolve(dist, ".openai/hosting.json"), "utf8"));
if (!hosting.project_id) {
  console.warn("hosting.json todavía no tiene project_id; crea el Site antes de empaquetar para publicar.");
}
if (hosting.d1 !== "DB") throw new Error("El binding D1 debe llamarse DB.");

