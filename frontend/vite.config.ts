import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      // The browser never talks to an external API directly: everything goes
      // through the FastAPI backend.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  // The same proxy for `vite preview`, so the production bundle can be
  // exercised against the real backend (dev-only React behaviour such as
  // StrictMode's double effect invocation does not apply there).
  preview: {
    port: 4173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 1200 },
})
