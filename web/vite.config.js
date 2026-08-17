import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";

// Build → web/dist, which server.py serves. In dev, proxy /api to the running
// upscale-ui server so `pnpm dev` is fully live.
//
// Port 8790 keeps this clear of the usage dashboard (8787) and its collector
// (8788). This is a SEPARATE service by design: it shares no port, no unit and
// no repo with that dashboard.
const API = process.env.UPSCALE_UI_API || "http://127.0.0.1:8790";

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    proxy: { "/api": { target: API, changeOrigin: true } },
  },
});
