import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// Мобильный режим: офлайн обязателен — связь на стройке нестабильна.
export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'НАДЗОР.ИИ — предиктивный строительный надзор',
        short_name: 'НАДЗОР.ИИ',
        description: 'Карта внимания инспектора и оценка гипотез на объекте',
        theme_color: '#5B5BD6',
        background_color: '#F5F6F8',
        display: 'standalone',
        start_url: '/',
        icons: [{ src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png}'],
        runtimeCaching: [
          {
            // Карта внимания и гипотезы должны открываться без связи.
            urlPattern: /\/api\/(attention|findings|objects)/,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'nadzor-api',
              expiration: { maxEntries: 200, maxAgeSeconds: 60 * 60 * 24 * 14 },
            },
          },
          {
            urlPattern: /\/api\/documents\/.*\/page\//,
            handler: 'CacheFirst',
            options: { cacheName: 'nadzor-pages', expiration: { maxEntries: 120 } },
          },
        ],
      },
    }),
  ],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': { target: process.env.VITE_API_URL || 'http://localhost:8000', changeOrigin: true },
      // Отдельный лёгкий бэкенд (packages/backend) — сравнение документов,
      // без RBAC/аудита; см. NewAnalysis.tsx и backendApi.ts.
      '/backend': {
        target: process.env.VITE_BACKEND_URL || 'http://localhost:8010',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/backend/, ''),
      },
    },
  },
})
