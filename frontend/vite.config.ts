import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /tiles is forwarded to the Martin tile server from docker-compose.yml, /api to PostgREST.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/tiles": {
        target: "http://localhost:3100",
        rewrite: (path) => path.replace(/^\/tiles/, ""),
      },
      "/api": {
        target: "http://localhost:3110",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
