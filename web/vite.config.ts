import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import type { Plugin } from "vite";
import vue from "@vitejs/plugin-vue";
import tailwind from "@tailwindcss/vite";

const root = fileURLToPath(new URL(".", import.meta.url));

/**
 * Subresource Integrity for the built entry document.
 *
 * The CSP already restricts scripts to this origin, so SRI is defence in depth:
 * if a file on disk (or a CDN in front of it) is swapped, the browser refuses
 * to execute bytes that do not match the hash the document was built with. Vite
 * does not emit integrity by default, and doing it after the bundle is written
 * is the only point where the final content hashes exist.
 */
function subresourceIntegrity(): Plugin {
  return {
    name: "wsctl-sri",
    apply: "build",
    closeBundle() {
      const outDir = process.env.WSCTL_WEB_OUTDIR
        ? resolve(process.env.WSCTL_WEB_OUTDIR)
        : resolve(root, "../src/wsctl/static");
      const indexPath = resolve(outDir, "index.html");
      let html: string;
      try {
        html = readFileSync(indexPath, "utf8");
      } catch {
        return;
      }
      html = html.replace(
        /(src|href)="\/static\/(assets\/[^"]+\.(?:js|css))"/g,
        (match, attribute: string, relative: string) => {
          const file = resolve(outDir, relative);
          const digest = createHash("sha384").update(readFileSync(file)).digest("base64");
          return `${attribute}="/static/${relative}" integrity="sha384-${digest}"`;
        },
      );
      writeFileSync(indexPath, html);
    },
  };
}

export default defineConfig({
  base: "/static/",
  plugins: [vue(), tailwind(), subresourceIntegrity()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  build: {
    outDir: process.env.WSCTL_WEB_OUTDIR || "../src/wsctl/static",
    emptyOutDir: true,
    // Source maps expose the whole frontend and added ~7.6 MiB to the wheel.
    // Keep them for an explicit debugging build, not for release artifacts.
    sourcemap: process.env.WSCTL_WEB_SOURCEMAP === "1",
    target: "es2022",
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes("@xterm")) return "terminal";
          if (id.includes("naive-ui")) return "ui";
          if (id.includes("@codemirror") || id.includes("/codemirror/")) return "editor";
          if (id.includes("asciinema-player")) return "replay";
          return undefined;
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:7681", ws: false },
      "/ws": { target: "ws://127.0.0.1:7681", ws: true },
      "/healthz": { target: "http://127.0.0.1:7681" },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.spec.ts"],
    setupFiles: ["./src/test/setup.ts"],
  },
});
