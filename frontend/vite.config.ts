import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    // Bind IPv4 explicitly. On Windows `localhost` resolves to IPv6 ::1 first, so
    // Vite listened on [::1] only and a tunnel forwarding to 127.0.0.1 got
    // "connection refused" (Cloudflare error 530). Browsers still reach
    // http://localhost:5173 because they fall back to IPv4.
    host: '127.0.0.1',
    // Phones need HTTPS for web push, which in development means reaching this
    // server through a tunnel. Vite rejects unknown Host headers by default, so
    // the tunnel's hostname has to be allowed or the phone gets "Blocked request".
    allowedHosts: ['.trycloudflare.com', '.ngrok-free.app'],
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
