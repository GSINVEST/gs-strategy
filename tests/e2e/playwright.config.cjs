const { defineConfig } = require('../../strategies/_common/graph/ui/node_modules/@playwright/test');
const path = require('node:path');
const os = require('node:os');
const root = path.resolve(__dirname, '../..');
const python = path.join(root, process.platform === 'win32' ? '.venv-bt/Scripts/python.exe' : '.venv-bt/bin/python');

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: '*.spec.cjs',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120000,
  expect: { timeout: 60000 },
  reporter: 'list',
  outputDir: path.join(os.tmpdir(), 'live-strategy-graph-e2e'),
  use: {
    baseURL: 'http://127.0.0.1:19102',
    viewport: { width: 1280, height: 800 },
    browserName: 'chromium',
    launchOptions: process.env.GRAPH_E2E_CHROME ? { executablePath: process.env.GRAPH_E2E_CHROME } : {},
    reducedMotion: 'reduce',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `"${python}" -m strategies._common.graph ui --fixture --port 19102`,
    cwd: root,
    env: { PYTHONUTF8: '1' },
    url: 'http://127.0.0.1:19102/api/session',
    reuseExistingServer: Boolean(process.env.GRAPH_E2E_REUSE),
    timeout: 180000,
    stdout: 'ignore',
    stderr: 'ignore',
  },
});
