import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/proxy-api': {
        target: 'http://localhost:25792',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/proxy-api/, '')
      },
      '/py-api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/py-api/, '')
      }
    }
  }
})
