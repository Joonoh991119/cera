import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { cpSync } from "node:fs";
import { resolve } from "node:path";

// The build output is static files served from the same origin; they run under the server CSP (script-src and style-src 'self').
export default defineConfig({
  base: "/",
  plugins: [
    react(),
    {
      // Excalidraw also looks for fonts on esm.sh after EXCALIDRAW_ASSET_PATH; the CSP blocks that, so redirect to the same origin (a nonexistent path)
      name: "no-cdn-font-fallback",
      renderChunk(code) {
        return code.includes("https://esm.sh/") ? { code: code.split("https://esm.sh/").join("${window.location.origin}/__no-cdn__/"), map: null } : null;
      },
    },
    {
      name: "copy-excalidraw-fonts",
      closeBundle() {
        cpSync(resolve("node_modules/@excalidraw/excalidraw/dist/prod/fonts"), resolve("dist/fonts"), { recursive: true });
      },
    },
  ],
  define: { "process.env.IS_PREACT": JSON.stringify("false") },
  build: { outDir: "dist", emptyOutDir: true, assetsInlineLimit: 0, chunkSizeWarningLimit: 4000, sourcemap: false },
  server: { proxy: { "/api": "http://127.0.0.1:8790" } },
});
