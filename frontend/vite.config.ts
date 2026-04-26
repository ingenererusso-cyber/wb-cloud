import path from "node:path"
import { fileURLToPath } from "node:url"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// Django: {% static 'frontend/...' %} — base совпадает с STATIC_URL + 'frontend/'
const DJANGO_BASE = "/static/frontend/"

export default defineConfig({
  base: process.env.VITE_DJANGO_BASE === "1" ? DJANGO_BASE : "/",
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: path.resolve(__dirname, "../static/frontend"),
    emptyOutDir: true,
  },
  server: {
    port: 5173,
  },
})
