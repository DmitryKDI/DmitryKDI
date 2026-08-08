import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { VitePWA } from 'vite-plugin-pwa';

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['icons/icon.svg'],
      manifest: {
        name: 'ИИ-бизнес-ассистент — ремонт и экспертиза',
        short_name: 'Бизнес-ассистент',
        description: 'Задачи, сделки, отчёты и голосовой/текстовый ассистент в одном месте (демо)',
        theme_color: '#0b0d14',
        background_color: '#0b0d14',
        display: 'standalone',
        start_url: '/',
        icons: [
          { src: '/icons/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
          { src: '/icons/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'maskable' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg}'],
      },
    }),
  ],
  server: {
    proxy: {
      '/api': 'http://localhost:4000',
    },
  },
});
