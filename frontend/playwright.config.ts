import { defineConfig } from '@playwright/test'
import { existsSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

// Each run owns its runtime data.  Browser tests must never read or mutate a
// developer's real conversations, memories, or active persona.
//
// Playwright 会多次求值本配置文件（main 与 webServer 插件各一次），模块级
// mkdtemp 每次求值都会造一个新目录——webServer 的后端和 spec 里的种子脚本会
// 因此写进不同目录（真实踩过：一个 11 个文件、另一个 0 个）。解法：以
// 「datadir 指针文件」为准。config 不感知 run 边界，改由 npm script 在每次
// `playwright test` 前删除指针文件（test:e2e），使每次 run 的首次求值重建。
const pointerPath = resolve(import.meta.dirname, '../.tmp/e2e-datadir')
let dataDir: string
if (existsSync(pointerPath)) {
  dataDir = readFileSync(pointerPath, 'utf-8').trim()
} else {
  dataDir = mkdtempSync(join(tmpdir(), 'tztuzhan-e2e-'))
  writeFileSync(pointerPath, dataDir)
}
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
