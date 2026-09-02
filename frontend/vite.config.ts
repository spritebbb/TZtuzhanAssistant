import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import electron from 'vite-plugin-electron/simple'

export default defineConfig({
  plugins: [
    vue(),
    electron({
      main: {
        entry: 'electron/main.ts',
      },
      preload: {
        input: 'electron/preload.ts',
      },
      renderer: {},
    }),
  ],
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
