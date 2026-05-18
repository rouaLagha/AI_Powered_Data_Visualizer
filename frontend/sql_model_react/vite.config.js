import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    port: 3000,
    open: true,
    host: 'localhost',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5177',
        changeOrigin: true
      },
      '/schema-flow': {
        target: 'http://127.0.0.1:5177',
        changeOrigin: true
      }
    }
  }
})
