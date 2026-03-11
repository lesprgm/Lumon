import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const SECURITY_HEADERS = {
  "Content-Security-Policy":
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' http://127.0.0.1:8000 ws://127.0.0.1:8000 http://localhost:8000 ws://localhost:8000; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none';",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    headers: SECURITY_HEADERS,
    fs: {
      allow: [".."],
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    headers: SECURITY_HEADERS,
  },
});
