import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Tauri devUrl 과 일치
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: '127.0.0.1',
  },
  envPrefix: ['VITE_', 'TAURI_'],
})
