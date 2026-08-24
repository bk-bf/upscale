import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/kit').Config} */
export default {
  preprocess: vitePreprocess(),
  kit: {
    // Client-only SPA: every route reads /api at runtime, so nothing is prerenderable.
    // Output lands in `dist/` because that's the directory server.py serves, and its
    // static handler already falls back to index.html for unknown paths — exactly what
    // SPA mode needs for deep links like /git.
    adapter: adapter({ pages: "dist", assets: "dist", fallback: "index.html", strict: false }),
  },
};
