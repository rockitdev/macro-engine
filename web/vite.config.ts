import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    // Allow HMR through Caddy wildcard proxy and Tailscale
    allowedHosts: [
      "macro-engine.dev.rockitwebhosting.net",
      "moore.tailc24dca.ts.net",
    ],
    proxy: {
      // When running inside Docker (docker-compose.dev.yml), VITE_API_TARGET is
      // set to http://api:8787 (the compose service name). Outside Docker it falls
      // back to the local Python backend on 127.0.0.1:8787.
      "/api": process.env.VITE_API_TARGET ?? "http://127.0.0.1:8787",
    },
  },
})
