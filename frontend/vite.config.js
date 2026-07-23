import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';

const certPath = '../certs/localhost.crt';
const keyPath = '../certs/localhost.key';
const https = fs.existsSync(certPath) && fs.existsSync(keyPath)
  ? {cert: fs.readFileSync(certPath), key: fs.readFileSync(keyPath)}
  : undefined;

export default defineConfig({
  plugins: [react()],
  server: {
    https,
    proxy: {
      '/api': {
        target: 'https://127.0.0.1:8000',
        changeOrigin: true,
        secure: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
});
