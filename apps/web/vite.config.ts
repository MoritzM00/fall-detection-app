import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    // The UI is same-origin. Vite's default CORS would grant preflights to other
    // localhost pages, letting them send the API's client header through /api.
    cors: false,
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${process.env.FALL_DETECTION_API_PORT ?? "8000"}`,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
