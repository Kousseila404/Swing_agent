import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// P7 — le silenceur de console est géré dans main.jsx (if import.meta.env.PROD),
// car Vite v8 utilise oxc et ignore l'option esbuild.drop.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/charts': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  // Vitest — la majorité des tests utils tournent en node, mais les tests
  // de hooks (useHashSearchParams, etc.) ont besoin de window/document.
  // jsdom est plus léger que happy-dom et suffisant pour notre surface.
  test: {
    environment: 'jsdom',
    globals: false,
  },
})
