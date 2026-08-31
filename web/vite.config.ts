import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.API_PROXY_TARGET || 'http://localhost:8181',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
        // 浏览器只访问同源 /minio；代理目标必须与后端 MINIO_ENDPOINT 一致。
        '/minio': {
          target: env.MINIO_PROXY_TARGET || 'http://localhost:9000',
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/minio/, ''),
        },
      },
    },
  }
})
