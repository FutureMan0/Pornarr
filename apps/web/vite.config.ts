import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

/**
 * The SPA is built into `dist/` and copied into the API image at
 * `apps/api/pornarr_api/static/`, which serves `/assets` and falls back to
 * index.html. Nothing runs Node in production.
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  return {
    plugins: [react(), tailwindcss()],

    // Sub-path deployment behind a reverse proxy: the API normalises the same
    // value into FastAPI's root_path, so both sides must read one variable.
    base: env.BASE_PATH ? `${env.BASE_PATH}/` : "/",

    server: {
      port: 5173,
      host: true,
      // The API sets no CORS headers, and the session cookie is SameSite=lax on
      // path=/api. Proxying keeps the browser on one origin, which is the only
      // arrangement where the cookie is both sent and accepted in development.
      proxy: {
        "/api": {
          target: env.VITE_API_URL || "http://localhost:8000",
          changeOrigin: false,
        },
      },
    },

    build: {
      outDir: "dist",
      // The API mounts dist/assets as a StaticFiles directory and serves
      // nothing else from the build, so every emitted file has to land there.
      assetsDir: "assets",
      sourcemap: true,
    },
  };
});
