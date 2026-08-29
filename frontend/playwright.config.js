import { defineConfig } from '@playwright/test';
import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const venvPython = resolve(
  import.meta.dirname,
  '..',
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
);
const python = existsSync(venvPython) ? `"${venvPython}"` : 'python3';

export default defineConfig({
  testDir: './tests',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    headless: true,
  },
  webServer: {
    command: `${python} -m http.server 4173 --directory .`,
    port: 4173,
    reuseExistingServer: !process.env.CI,
  },
});
