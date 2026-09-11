import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");

  return {
    plugins: [vue()],
    server: {
      port: Number(env.VITE_PORT || 5173),
      proxy: {
        "/api": env.VITE_API_TARGET || "http://localhost:8000",
      },
    },
  };
});
