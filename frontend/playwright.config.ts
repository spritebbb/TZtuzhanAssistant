import { defineConfig } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// Each run owns its runtime data.  Browser tests must never read or mutate a
// developer's real conversations, memories, or active persona.
const dataDir = mkdtempSync(join(tmpdir(), 'tztuzhan-e2e-'))
const projectRoot = resolve(import.meta.dirname, '..')
const python = join(projectRoot, '.venv', 'Scripts', 'python.exe')

export default defineConfig({
  testDir: './e2e',
  outputDir: '../.tmp/playwright-results',
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  use: {
    baseURL: 'http://127.0.0.1:8811',
    browserName: 'chromium',
    headless: true,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `\"${python}\" -m backend.main --host 127.0.0.1 --port 8811`,
    cwd: projectRoot,
    env: {
      ...process.env,
      TZTUZHAN_DATA_DIR: dataDir,
      MEMORY_V2: '0',
      MEMORY_MEM0: '0',
      TZT_BIND_HOST: '127.0.0.1',
      AGENT_ALLOWED_HOSTS: '127.0.0.1:8811;localhost:8811',
    },
    url: 'http://127.0.0.1:8811/api/health',
    timeout: 30_000,
    reuseExistingServer: false,
  },
})
