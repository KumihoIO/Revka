import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import { readFileSync } from "node:fs";

const gatewayTarget = process.env.REVKA_GATEWAY_URL ?? "http://127.0.0.1:42617";
const packageJson = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8")) as { version: string };

// Build-only config. The web dashboard is served by the Rust gateway
// via rust-embed. Run `npm run build` then `cargo build` to update.
export default defineConfig({
  base: "/_app/",
  plugins: [react(), tailwindcss()],
  define: {
    __REVKA_VERSION__: JSON.stringify(packageJson.version),
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": gatewayTarget,
      "/admin": gatewayTarget,
      "/pair": gatewayTarget,
      "/health": gatewayTarget,
      "/ws": {
        target: gatewayTarget,
        ws: true,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "dist",
  },
});
