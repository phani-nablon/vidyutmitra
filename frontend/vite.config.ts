import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// `npm run build` writes straight into the Python package, so `sr serve` serves the workbench at /
// with no Node at runtime. `npm run dev` proxies /api to a running `sr serve`.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../src/substation_retrieval/api/web',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8077', changeOrigin: true },
    },
  },
});
