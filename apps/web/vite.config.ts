import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${process.env.API_PORT ?? 8787}`,
        // The runtime only accepts its own Host (ADR 0001). The Origin header is left alone.
        changeOrigin: true,
      },
    },
  },
  build: {
    // The wheel picks the bundle up from here, as package data of the runtime.
    outDir: "../local-api/local_api/ui",
    emptyOutDir: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
  },
});
