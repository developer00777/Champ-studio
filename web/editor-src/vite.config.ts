import { defineConfig } from "vite";

// Built into web/editor/ (served by the engine at /editor/). `npm run dev` proxies the API.
export default defineConfig({
  base: "/editor/",
  build: { outDir: "dist", emptyOutDir: true, sourcemap: true },
  // dev: proxy the API to a local engine (CHAMP_API overrides; the dev container listens on 8790)
  server: { proxy: { "/api": process.env.CHAMP_API ?? "http://127.0.0.1:8790" } },
  test: { include: ["tests/**/*.test.ts"] },
});
