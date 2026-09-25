// Application entry. Created once by flamin generate; this file is open and yours to edit.
import express from "express";
import { existsSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export async function createApp() {
  const app = express();
  app.use(express.json());
  const base = join(import.meta.dirname, "modules");
  for (const mod of existsSync(base) ? readdirSync(base).sort() : []) {
    const dir = join(base, mod, "interface");
    for (const file of existsSync(dir) ? readdirSync(dir).sort() : []) {
      if (file.endsWith("Route.js")) {
        const route = await import(pathToFileURL(join(dir, file)).href);
        route.register(app);
      }
    }
  }
  return app;
}
