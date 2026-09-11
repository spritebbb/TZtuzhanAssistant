import { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage, Notification, dialog, shell } from 'electron'
import { ChildProcess, spawn } from 'child_process'
import { dirname, join, resolve } from 'path'
import { existsSync } from 'fs'
import { fileURLToPath } from 'url'
import http from 'http'

// ESM 模式没有 __dirname，用 import.meta.url 推导（指向 dist-electron/ 或 electron/ 源码目录）
const _dirname = dirname(fileURLToPath(import.meta.url))

let mainWindow: BrowserWindow | null = null
let tray: Tray | null = null
let backendProcess: ChildProcess | null = null
let isQuitting = false

// 后端端口
const BACKEND_PORT = 8801
const BACKEND_HOST = `http://127.0.0.1:${BACKEND_PORT}`

// 主进程主动消息轮询：让菟菚在窗口隐藏/关闭时也能弹系统通知。
// 渲染进程通过 IPC 上报「当前会话 id」，主进程据此轮询后端主动性接口，
// 拿到主动消息后直接弹系统通知（不依赖渲染进程存活），并转发给窗口追加气泡。
let activeSessionId: string | null = null
let initiativeTimer: ReturnType<typeof setInterval> | null = null
const INITIATIVE_POLL_MS = 30000
let lastNotifiedText = ''  // 已通知过的消息去重
let archiveDone = false    // 退出归档只执行一次（避免托盘退出 + before-quit 重复归档）

interface ProactiveMessage {
  text: string
  image?: string | null
}

/** 拉取当前会话的菟菚主动消息（轮询后端 /api/initiative） */
async function pollInitiative(): Promise<void> {
  if (!activeSessionId) return
  try {
    const resp = await fetch(`${BACKEND_HOST}/api/initiative?session_id=${encodeURIComponent(activeSessionId)}`)
    const data = await resp.json()
    const fallbackText: unknown = data?.initiative
    const rawMessage: unknown = data?.message
    const message: ProactiveMessage | null = rawMessage && typeof rawMessage === 'object'
      && typeof (rawMessage as ProactiveMessage).text === 'string'
      ? rawMessage as ProactiveMessage
      : typeof fallbackText === 'string' ? { text: fallbackText, image: null } : null
    const text = message?.text ?? null
    if (!text || text === lastNotifiedText) return
    lastNotifiedText = text

    // 1) 系统通知（关窗也能弹，因为这里是主进程）
    if (Notification.isSupported()) {
      const n = new Notification({ title: '菟菚', body: text, silent: false })
      n.on('click', () => {
        if (!mainWindow) return
        if (mainWindow.isMinimized()) mainWindow.restore()
        mainWindow.show()
        mainWindow.focus()
      })
      n.show()
    }
    // 2) 窗口开着时，把消息转发给渲染进程追加气泡
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('initiative-message', message)
    }
  } catch {
    // 后端未就绪/网络抖动：静默，下次轮询再试
  }
}

function startInitiativePolling(): void {
  if (initiativeTimer) return
  initiativeTimer = setInterval(pollInitiative, INITIATIVE_POLL_MS)
}

function stopInitiativePolling(): void {
  if (initiativeTimer) {
    clearInterval(initiativeTimer)
    initiativeTimer = null
  }
}

/** 检测后端端口是否已响应 */
function checkBackend(): Promise<boolean> {
  return new Promise((resolve_) => {
    const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/api/health`, (res) => {
      res.resume()
      resolve_(res.statusCode === 200)
    })
    req.on('error', () => resolve_(false))
    req.setTimeout(3000, () => { req.destroy(); resolve_(false) })
  })
}

/** 是否是应用自身的页面（后端同源页 / 开发服务器 / data: 兜底页）。 */
function isInternalUrl(url: string): boolean {
  if (url.startsWith(BACKEND_HOST) || url.startsWith('data:')) return true
  const dev = process.env.VITE_DEV_SERVER_URL
  return !!dev && url.startsWith(dev)
}

/** 启动后端 Python 进程。
 *
 * 探测链（按序，找到即用）：
 * 1. 打包版：resources/backend/.venv/Scripts/python.exe（随包 venv）；
 *    开发版：项目根 .venv/Scripts/python.exe
 * 2. 系统 PATH 上的 python（与 Start-Tuzhan.bat 同口径）；
 *    此时若依赖未装，自动 pip install -r requirements.txt（只装一次，成功后留标记）
 * 后两者对应 web 部署包的「一键启动器」语义：没有随包 venv 也能自行拉起。
 */
function resolveBackendLauncher(rootDir: string, isDev: boolean): { exe: string; needsInstall: boolean } | null {
  const script = join(rootDir, 'backend', 'main.py')
  if (!existsSync(script)) return null
  const bundledVenvPython = isDev
    ? join(rootDir, '.venv', 'Scripts', 'python.exe')
    : join(rootDir, 'backend', '.venv', 'Scripts', 'python.exe')
  if (existsSync(bundledVenvPython)) return { exe: bundledVenvPython, needsInstall: false }
  const pathDirs = (process.env.PATH ?? '').split(';').filter(Boolean)
  for (const dir of pathDirs) {
    const candidate = join(dir, 'python.exe')
    if (existsSync(candidate)) return { exe: candidate, needsInstall: true }
  }
  return null
}

/** 标记文件：系统 Python 路径下依赖已装过（venv 无此问题，依赖随 venv 判定） */
function depsMarkerPath(rootDir: string): string {
  return join(rootDir, '.deps-installed')
}

function pipInstall(exe: string, rootDir: string): Promise<boolean> {
  return new Promise((resolve_) => {
    const proc = spawn(exe, ['-m', 'pip', 'install', '-r', join(rootDir, 'requirements.txt')], {
      cwd: rootDir,
      stdio: 'pipe',
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    })
    proc.on('error', () => resolve_(false))
    proc.on('exit', (code) => resolve_(code === 0))
  })
}
async function startBackend(): Promise<boolean> {
  const isDev = !app.isPackaged
  // 开发模式：_dirname = dist-electron/（或 electron/），需向上两级到项目根
  const rootDir = isDev ? resolve(_dirname, '../..') : process.resourcesPath

  // 先检查后端是否已经在运行
  if (await checkBackend()) {
    console.log('[electron] 后端已在运行，跳过启动')
    return true
  }

  const launcher = resolveBackendLauncher(rootDir, isDev)
  if (!launcher) {
    const hasScript = existsSync(join(rootDir, 'backend', 'main.py'))
    console.warn(
      hasScript
        ? '[electron] 未找到可用的 Python（随包 venv 与系统 PATH 均无）。请安装 Python 3.11-3.13（勾选 Add to PATH）后重试，或手动启动后端：python backend/main.py'
        : '[electron] 后端未随应用打包（backend/main.py 不存在），请先单独启动后端：python backend/main.py',
    )
    return false
  }
  if (launcher.needsInstall && !existsSync(depsMarkerPath(rootDir))) {
    console.log('[electron] 首次使用系统 Python：安装后端依赖（可能需要几分钟）...')
    const ok = await pipInstall(launcher.exe, rootDir)
    if (!ok) {
      console.warn('[electron] 依赖安装失败，请检查网络后重试，或手动执行 pip install -r requirements.txt')
      return false
    }
    try {
      const { writeFileSync } = await import('fs')
      writeFileSync(depsMarkerPath(rootDir), new Date().toISOString())
    } catch { /* 标记写失败只是下次多装一次依赖，不致命 */ }
  }

  console.log('[electron] 启动后端...')
  backendProcess = spawn(launcher.exe, [join(rootDir, 'backend', 'main.py'), '--host', '127.0.0.1', '--port', String(BACKEND_PORT)], {
    cwd: rootDir,
    stdio: 'pipe',
    env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
  })

  const spawnedProcess = backendProcess
  let spawnFailed = false
  spawnedProcess.on('error', (error) => {
    spawnFailed = true
    console.error('[backend] 启动失败：', error)
    if (backendProcess === spawnedProcess) backendProcess = null
  })
  backendProcess.stdout?.on('data', (data: Buffer) => {
    console.log(`[backend] ${data.toString().trim()}`)
  })
  backendProcess.stderr?.on('data', (data: Buffer) => {
    console.error(`[backend] ${data.toString().trim()}`)
  })
  backendProcess.on('exit', (code) => {
    console.log(`[backend] 进程退出 (code: ${code})`)
    backendProcess = null
  })
  for (let i = 0; i < 60; i++) {
    if (spawnFailed) return false
    if (await checkBackend()) return true
    await new Promise(resolve_ => setTimeout(resolve_, 500))
  }
  stopBackend()
  return false
}

function stopBackend(): void {
  if (backendProcess) {
    try {
      const pid = backendProcess.pid
      // 优雅关闭：让后端先做 checkpoint + 备份（强杀兜底留足时间）。
      // 后端 /api/health/shutdown 会先返回 200，再后台执行 checkpoint+备份+退出；
      // 这里给足 8s 兜底窗口——备份含 imgs/screenshots 目录拷贝，可能较慢，
      // 过早强杀会丢最后一次备份。后端正常退出后 backendProcess 会被 on('exit')
      // 置 null，下方 setTimeout 里的判断会跳过强杀。
      if (pid) {
        const req = http.request(
          { host: '127.0.0.1', port: 8801, path: '/api/health/shutdown', method: 'POST', timeout: 500 },
          (res) => res.resume()
        )
        req.on('error', () => {})
        req.on('timeout', () => req.destroy())
        req.end()
      }
    } catch { /* ignore */ }
    setTimeout(() => {
      if (backendProcess) {
        backendProcess.kill()
        backendProcess = null
      }
    }, 8000)
  }
}

function createWindow(backendReady = true): void {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 720,
    minWidth: 780,
    minHeight: 500,
    title: '菟菚 · 桌面助手',
    icon: join(_dirname, '../public/icon.png'),
    webPreferences: {
      preload: join(_dirname, 'preload.mjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    show: false,
    backgroundColor: '#f6f9f7',
  })

  // 阻止深色模式下白色闪烁
  mainWindow.on('ready-to-show', () => {
    mainWindow?.show()
  })

  // 外链一律交给系统浏览器：既不把聊天界面顶掉（窗口内导航没有后退入口，
  // 用户会卡在外部页面），也不在应用里另开一个无地址栏的窗口。
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!isInternalUrl(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (isInternalUrl(url)) return
    event.preventDefault()
    void shell.openExternal(url)
  })

  // 关闭按钮 = 隐藏到托盘（任务栏常驻）；只有托盘菜单"退出"才真正退出。
  // 之前 window-all-closed 直接 stopBackend+quit，导致托盘"显示窗口"永远用不上
  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault()
      mainWindow?.hide()
    }
  })

  // 开发模式加载 Vite 开发服务器，生产模式加载后端服务（同源，Origin 可信；
  // file:// 的 Origin 是 null，会被后端 CORS/Origin 守卫拒绝，不可用）
  if (!backendReady) {
    const html = '<!doctype html><meta charset="utf-8"><style>body{font:16px sans-serif;line-height:1.8;padding:40px;color:#24342d}code{background:#eee;padding:2px 6px}</style><h2>菟菚后端未能启动</h2><p>请按顺序检查：</p><ol><li>确认 <code>resources/backend/</code> 目录完整（含 <code>main.py</code> 与 <code>requirements.txt</code>）；</li><li>未随包附带 Python 时，需已安装 Python 3.11–3.13 并勾选「Add to PATH」；</li><li>首次启动会自动安装依赖，如网络受限可手动执行 <code>pip install -r resources/backend/requirements.txt</code>。</li></ol>'
    mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`)
  } else if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
  } else {
    mainWindow.loadURL(BACKEND_HOST)
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

function createTray(): void {
  // 从立绘 PNG 生成托盘图标（缩放至 16x16）
  const isDev = !app.isPackaged
  // 开发：项目根 assets/；打包：app.asar 内的 assets/（随 build.files 分发）
  const personaPath = isDev
    ? join(resolve(_dirname, '../..'), 'assets', 'persona.png')
    : join(_dirname, '../assets/persona.png')
  let icon: Electron.NativeImage
  if (existsSync(personaPath)) {
    icon = nativeImage.createFromPath(personaPath).resize({ width: 16, height: 16 })
  } else {
    icon = nativeImage.createEmpty()
  }
  tray = new Tray(icon)
  tray.setToolTip('菟菚桌面助手')
  const contextMenu = Menu.buildFromTemplate([
    {
      label: '显示窗口',
      click: () => {
        if (!mainWindow) return
        if (mainWindow.isMinimized()) mainWindow.restore()
        mainWindow.show()
        mainWindow.focus()
      },
    },
    { type: 'separator' },
    {
      label: '退出',
      click: async () => {
        isQuitting = true
        // 先归档当前会话，等归档完成后再停后端，避免杀进程过早导致归档丢失
        await archiveSessionOnQuit()
        stopBackend()
        app.quit()
      },
    },
  ])
  tray.setContextMenu(contextMenu)
  tray.on('click', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  })
}

// IPC 处理
ipcMain.handle('get-backend-url', () => BACKEND_HOST)
ipcMain.handle('get-version', () => app.getVersion())
ipcMain.handle('notify', (_e, { title, body }: { title: string; body: string }) => {
  // 系统通知：菟菚主动消息。点击通知 → 聚焦并显示窗口。
  // 去重：与轮询通道（pollInitiative）共享 lastNotifiedText，避免「渲染进程 SSE
  // 先消费 + 主进程轮询后到」时同一条消息弹两次。谁先到都只弹一次。
  if (body === lastNotifiedText) return false
  lastNotifiedText = body
  if (Notification.isSupported()) {
    const n = new Notification({ title, body, silent: false })
    n.on('click', () => {
      if (!mainWindow) return
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.show()
      mainWindow.focus()
    })
    n.show()
    return true
  }
  return false
})
ipcMain.handle('focus-window', () => {
  if (!mainWindow) return false
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
  return true
})
// 渲染进程上报「当前会话 id」，主进程据此轮询主动消息（关窗也能弹通知）
ipcMain.handle('set-active-session', (_e, sessionId: string | null) => {
  activeSessionId = sessionId
  if (sessionId) {
    startInitiativePolling()
    // 立即轮询一次，减少切换会话后的空窗期
    pollInitiative()
  } else {
    stopInitiativePolling()
  }
  return true
})

app.whenReady().then(async () => {
  const backendReady = await startBackend()
  if (!backendReady) dialog.showErrorBox('菟菚后端启动失败', '未找到后端文件或可用 Python，或后端在 30 秒内未能启动。详情见主窗口的排查清单。')
  createWindow(backendReady)
  createTray()

  app.on('activate', async () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(await checkBackend())
  })
})

app.on('window-all-closed', () => {
  // 正常关闭窗口已改为隐藏到托盘，只有真正退出（isQuitting）才会走到这里
  if (isQuitting) {
    stopBackend()
    if (process.platform !== 'darwin') app.quit()
  }
})

app.on('before-quit', () => {
  isQuitting = true
  // 先归档当前会话，等归档请求结束后再停后端，避免杀进程过早导致归档丢失。
  // 若托盘退出已归档过（archiveDone），则直接停后端。
  archiveSessionOnQuit().finally(() => stopBackend())
})

/** 退出前归档：把当前会话消息打包存入 archives 表（best-effort，失败静默；只执行一次） */
function archiveSessionOnQuit(): Promise<void> {
  if (archiveDone) return Promise.resolve()
  archiveDone = true
  return new Promise((resolve) => {
    try {
      const req = http.request(
        { host: '127.0.0.1', port: BACKEND_PORT, path: '/api/sessions/archive', method: 'POST', timeout: 2000 },
        () => resolve()
      )
      req.on('error', () => resolve())
      req.on('timeout', () => { req.destroy(); resolve() })
      req.end()
    } catch {
      resolve()
    }
  })
}
