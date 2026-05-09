import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    // Forzar optimización más rápida
    force: false,
    exclude: ['@radix-ui/react-*'],
  },
  server: {
    fs: {
      strict: false,
    },
    // Reducir el watch para archivos que no necesitan recarga
    watch: {
      ignored: ['**/node_modules/**', '**/.git/**'],
    },
  },
  build: {
    rollupOptions: {
      external: [],
    },
  },
  esbuild: {
    target: 'esnext'
  }
})
