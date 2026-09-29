import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

export default defineConfig({
  plugins: [solid()],
  server: {
    port: 5173,
    // changeOrigin rewrites Host to the API's own, which its Host allowlist accepts.
    proxy: { "/api": { target: "http://127.0.0.1:8787", changeOrigin: true } },
  },
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false },
});
