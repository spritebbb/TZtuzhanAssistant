// 桌面外壳冒烟（Electron 真实启动）：`npm run test:electron`
//
// 为什么需要它：Vitest 只覆盖渲染层逻辑；Playwright 的 e2e 跑的是浏览器 +
// 后端（webServer），两者都碰不到 Electron 主进程 / preload / IPC。于是
// 「Electron 大版本升级后外壳还能不能起来」此前只能靠人肉点击。本脚本启动
// **真实产物**（dist-electron/main.js → 打包前的主进程），逐项断言：
//   1) 主进程能起来，窗口能加载后端页面（同源 http://127.0.0.1:8801）；
//   2) preload 桥已注入且 IPC 往返可用（window.electronAPI.getVersion()）；
//   3) 渲染进程能连上随包拉起的后端（fetch /api/health → 200）；
//   4) 首屏挂载出真实 DOM（#app 有子节点）。
//
// 实现说明：不用 Playwright 的 ElectronApplication 驱动——它对 Electron 44 的
// 窗口发现不工作（app.windows() 恒为 0、firstWindow 超时）。改为自己带
// --remote-debugging-port 拉起 Electron，再用 chromium.connectOverCDP 连渲染
// 进程：既看得到真实页面，也不依赖上游那份窗口追踪。
//
// 隔离：TZTUZHAN_DATA_DIR 指向一次性临时目录，绝不读写项目 data/；启动前若
// 8801 已被占用就直接失败（否则会连上正在跑的真实后端）。
import { chromium } from 'playwright-core'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

const frontendDir = resolve(import.meta.dirname, '..')
const dataDir = mkdtempSync(join(tmpdir(), 'tztuzhan-electron-smoke-'))
const BACKEND_PORT = 8801
const pkg = JSON.parse(readFileSync(join(frontendDir, 'package.json'), 'utf-8'))
const electronPath = (await import('electron')).default

const failures = []
function check(name, ok, detail = '') {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` — ${detail}` : ''}`)
  if (!ok) failures.push(name)
}

function freePort() {
  return new Promise((done, fail) => {
    const srv = createServer()
    srv.once('error', fail)
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address()
      srv.close(() => done(port))
    })
  })
}

function portBusy(port) {
  return new Promise((done) => {
    const srv = createServer()
    srv.once('error', () => done(true))
    srv.once('listening', () => srv.close(() => done(false)))
    srv.listen(port, '127.0.0.1')
  })
}

async function fetchTargets(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  let lastError = 'no attempt'
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`)
      if (res.ok) {
        const targets = await res.json()
        const page = targets.find((t) => t.type === 'page' && t.url.startsWith(`http://127.0.0.1:${BACKEND_PORT}`))
        if (page) return page
        lastError = `targets=${targets.map((t) => `${t.type}:${t.url}`).join(',')}`
      } else {
        lastError = `http ${res.status}`
      }
    } catch (error) {
      lastError = String(error)
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error(`等待渲染进程目标超时（${timeoutMs}ms）：${lastError}`)
}

let child
let browser
let log = []
let exitCode = 0
try {
  console.log(`[electron-smoke] 数据目录: ${dataDir}`)
  if (await portBusy(BACKEND_PORT)) {
    throw new Error(`端口 ${BACKEND_PORT} 已被占用：请先关闭正在运行的菟菚实例，避免冒烟连到真实后端`)
  }
  const cdpPort = await freePort()
  child = spawn(electronPath, [`--remote-debugging-port=${cdpPort}`, '.'], {
    cwd: frontendDir,
    env: {
      ...process.env,
      TZTUZHAN_DATA_DIR: dataDir,
      TZT_BIND_HOST: '127.0.0.1',
      MEMORY_V2: '0',
      MEMORY_MEM0: '0',
      MEMORY_EMBED_FORCE: '1',
      PYTHONIOENCODING: 'utf-8',
    },
    stdio: 'pipe',
  })
  child.stdout?.on('data', (chunk) => log.push(String(chunk)))
  child.stderr?.on('data', (chunk) => log.push(String(chunk)))

  const target = await fetchTargets(cdpPort, 90_000)
  check('主进程启动并加载应用页面', true, `CDP target: ${target.url}`)

  browser = await chromium.connectOverCDP(`http://127.0.0.1:${cdpPort}`)
  const page = browser.contexts().flatMap((c) => c.pages()).find((p) => p.url().startsWith(`http://127.0.0.1:${BACKEND_PORT}`))
  if (!page) throw new Error('CDP 已连上但拿不到应用页面对象')
  await page.waitForLoadState('domcontentloaded', { timeout: 60_000 })

  const bridge = await page.evaluate(() => typeof window.electronAPI)
  check('preload 桥已注入', bridge === 'object', `typeof window.electronAPI=${bridge}`)

  const ipcVersion = await page.evaluate(() => window.electronAPI.getVersion())
  check('IPC 往返可用', ipcVersion === pkg.version, `renderer 读到 ${ipcVersion}（package.json ${pkg.version}）`)

  const health = await page.evaluate(async () => {
    try {
      return (await fetch('/api/health')).status
    } catch (error) {
      return `ERR:${error}`
    }
  })
  check('渲染进程连上随包后端', health === 200, `/api/health → ${health}`)

  await page.waitForFunction(() => (document.querySelector('#app')?.childElementCount ?? 0) > 0, { timeout: 60_000 })
  const mounted = await page.evaluate(() => ({
    title: document.title,
    children: document.querySelector('#app')?.childElementCount ?? 0,
  }))
  check('首屏挂载出真实 DOM', mounted.children > 0, `${mounted.title} #app 子节点 ${mounted.children}`)
} catch (error) {
  console.error(`[electron-smoke] 异常: ${error}`)
  if (log.length) {
    console.error('[electron-smoke] 主进程输出：')
    console.error(log.join('').split('\n').map((l) => `  | ${l}`).join('\n'))
  }
  failures.push(String(error))
} finally {
  try { await browser?.close() } catch { /* ignore */ }
  if (child?.pid) {
    // Electron 会派生渲染/GPU/后端子进程，整棵树一起收掉
    spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' })
  }
  try { rmSync(dataDir, { recursive: true, force: true }) } catch { /* ignore */ }
}

if (failures.length) {
  exitCode = 1
  console.error(`[electron-smoke] 失败 ${failures.length} 项：${failures.join(' / ')}`)
} else {
  console.log('[electron-smoke] 全部通过')
}
process.exit(exitCode)
