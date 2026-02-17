import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/dashboard/',
  server: {
    proxy: {
      '/dashboard/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
