const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  webServer: {
    command: 'npx http-server -p 8000',
    url: 'http://127.0.0.1:8000',
    reuseExistingServer: !process.env.CI,
    stdout: 'pipe',
    stderr: 'pipe',
  },
  use: {
    baseURL: 'http://127.0.0.1:8000',
  },
});
