import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
const target = process.env.RESUME_API_URL;
export default defineConfig({ plugins: [react()], server: {
  hmr: false, host: '127.0.0.1', port: Number(process.env.RESUME_WEB_PORT), strictPort: true,
  proxy: Object.fromEntries(['/api', '/annotations', '/fixture.epub', '/kosync', '/test-state'].map(p => [p, target!])),
}});
