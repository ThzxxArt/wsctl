import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

const root = resolve(process.cwd(), "..");
const configured = process.env.WSCTL_PYTHON || "python";
const python = configured.includes("/") ? resolve(process.cwd(), configured) : configured;

execFileSync(
  python,
  [resolve(root, "scripts/export_openapi.py"), "--output", resolve(root, "web/openapi.json")],
  { cwd: root, stdio: "inherit" },
);

execFileSync(
  "pnpm",
  ["exec", "openapi-typescript", "./openapi.json", "-o", "./src/api/schema.d.ts"],
  { cwd: process.cwd(), stdio: "inherit", shell: process.platform === "win32" },
);
