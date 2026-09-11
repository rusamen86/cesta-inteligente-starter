import net from "node:net";
import type { BridgeRequest } from "./types.js";

export type BridgeClient = (request: BridgeRequest) => Promise<unknown>;

export function createBridgeClient(socketPath: string, timeoutMs = 5_000): BridgeClient {
  if (!socketPath.startsWith("/") || socketPath.includes("\0")) {
    throw new Error("bridge socket must be an absolute operator-configured path");
  }
  return (request: BridgeRequest) =>
    new Promise((resolve, reject) => {
      const socket = net.createConnection({ path: socketPath });
      let response = "";
      const timer = setTimeout(() => {
        socket.destroy();
        reject(new Error("Cesta bridge timeout"));
      }, timeoutMs);
      const finish = (error?: Error, value?: unknown) => {
        clearTimeout(timer);
        socket.destroy();
        if (error) reject(error);
        else resolve(value);
      };
      socket.setEncoding("utf8");
      socket.on("connect", () => socket.write(`${JSON.stringify(request)}\n`));
      socket.on("data", (chunk: string) => {
        response += chunk;
        if (response.length > 1_048_576) return finish(new Error("Cesta bridge response too large"));
        const newline = response.indexOf("\n");
        if (newline < 0) return;
        try {
          const payload = JSON.parse(response.slice(0, newline)) as {
            ok: boolean;
            result?: unknown;
            error?: string;
            message?: string;
          };
          if (!payload.ok) return finish(new Error(`Cesta bridge rejected request: ${payload.error ?? "error"}`));
          finish(undefined, payload.result);
        } catch {
          finish(new Error("Invalid Cesta bridge response"));
        }
      });
      socket.on("error", (error) => finish(error));
    });
}
