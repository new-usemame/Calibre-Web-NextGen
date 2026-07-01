import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/static/app/',
  plugins: [react()],
  build: { outDir: '../cps/static/app', emptyOutDir: true },
  // Reverse-proxy prefix support (#571 follow-up). base:'/static/app/' is absolute
  // and gets baked into the runtime chunk loader, so lazily-imported JS/CSS (the
  // EPUB + native readers) were requested WITHOUT the proxy mount prefix and 404'd
  // behind a subpath — those pages rendered unstyled. For JS-computed asset URLs
  // (the chunk loader / modulepreload / dynamic CSS <link>), emit a runtime
  // expression that prepends window.__CWNG_PREFIX__ (injected by the server; empty
  // at the domain root). HTML-referenced assets (index.html's module script + main
  // CSS) keep the static base — the server rewrites those in the served shell.
  experimental: {
    renderBuiltUrl(filename, { hostType }) {
      if (hostType === 'js') {
        return { runtime: `(window.__CWNG_PREFIX__||"")+"/static/app/"+${JSON.stringify(filename)}` }
      }
      return { relative: false }
    },
  },
})
