import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const backendPort = Number(process.env.OMO_UI_PORT || 8765)
const frontendPort = Number(process.env.FRONTEND_PORT || 5173)

export default defineConfig({
  plugins: [vue()],
  server: {
    port: frontendPort,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${backendPort}`,
        changeOrigin: true,
      }
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  }
})
