import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";

const API = process.env.UPSCALE_UI_API || "http://127.0.0.1:8790";

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    proxy: { "/api": { target: API, changeOrigin: true } },
  },
});
