import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true
      }
    }
  },
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // 把体积大的第三方库拆成独立 chunk:
        // three.js 只被机柜页使用, 拆开后可长期缓存, 也不再拖累其他页面的包体
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('three')) return 'three'
          if (id.includes('echarts') || id.includes('zrender')) return 'echarts'
          if (id.includes('xlsx')) return 'xlsx'
          if (id.includes('element-plus') || id.includes('@element-plus')) return 'element-plus'
          return 'vendor'
        }
      }
    }
  }
})
