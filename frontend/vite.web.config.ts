import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Web 形态专用配置：不含 electron 插件。
// 用于浏览器预览前端 UI（不启动 Electron 壳），
// 规避本机 NODE_OPTIONS=--use-env-proxy 导致 electron 启动崩溃的问题。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    strictPort: false,
    watch: {
      ignored: ['**/*.tmpdir/**', '**/*.tmp'],
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
