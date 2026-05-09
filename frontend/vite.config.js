import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    // Remover force: true para evitar re-optimizaciones constantes
    // Agregar exclusiones si es necesario
    exclude: [],
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
})
