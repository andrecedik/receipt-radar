import path from "node:path"
import { fileURLToPath } from "node:url"
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const dirname = path.dirname(fileURLToPath(import.meta.url))

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Relative asset paths so assets resolve correctly regardless of where
  // the built site is served from; HashRouter (see src/App.tsx) keeps
  // routing client-side with no server rewrite rules needed. Still requires
  // an HTTP server (see web/README.md) -- unlike the plain-HTML site/, this
  // can't be opened directly via file://.
  base: './',
  resolve: {
    alias: {
      '@': path.resolve(dirname, './src'),
    },
  },
  server: {
    // Forwards Web Upload's /api/* calls to `receipt-radar serve` (default
    // 127.0.0.1:8000) during local dev, so the browser can call fetch("/api/...")
    // without a CORS/absolute-URL dance.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: '../site',
    emptyOutDir: true,
  },
  test: {
    include: ['src/**/*.test.{ts,tsx}', 'scripts/**/*.test.mjs'],
  },
})
