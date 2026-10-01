import { resolve } from 'path'
import { defineConfig } from 'vite'

export default defineConfig({
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        counties: resolve(__dirname, 'counties.html'),
        redFlags: resolve(__dirname, 'red-flags.html'),
        contract: resolve(__dirname, 'contract.html'),
        methodology: resolve(__dirname, 'methodology.html'),
        reports: resolve(__dirname, 'reports.html'),
        mobile: resolve(__dirname, 'mobile.html'),
      },
    },
  },
})
