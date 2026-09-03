import { app, BrowserWindow, ipcMain, Tray, Menu, nativeImage, Notification } from 'electron'
import { ChildProcess, spawn } from 'child_process'
import { join, resolve } from 'path'
import { existsSync } from 'fs'
import { fileURLToPath } from 'url'
import http from 'http'

// ESM 模式没有 __dirname，用 import.meta.url 推导（指向 dist-electron/ 或 electron/ 源码目录）
const _dirname = fileURLToPath(new URL('.', import.meta.url))

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

/** 拉取当前会话的菟菚主动消息（轮询后端 /api/initiative） */
async function pollInitiative(): Promise<void> {
  if (!activeSessionId) return
  try {
    const resp = await fetch(`${BACKEND_HOST}/api/initiative?session_id=${encodeURIComponent(activeSessionId)}`)
    const data = await resp.json()
    const text: string | null = data?.initiative ?? null
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
      mainWindow.webContents.send('initiative-message', text)
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
      resolve_(res.statusCode === 200)
    })
    req.on('error', () => resolve_(false))
    req.setTimeout(3000, () => { req.destroy(); resolve_(false) })
  })
}

/** 启动后端 Python 进程 */
async function startBackend(): Promise<void> {
  const isDev = !app.isPackaged
  // 开发模式：_dirname = dist-electron/（或 electron/），需向上两级到项目根
  const rootDir = isDev ? resolve(_dirname, '../..') : process.resourcesPath
  const python = isDev ? join(rootDir, '.venv', 'Scripts', 'python.exe') : join(process.resourcesPath, 'backend', 'python.exe')
  const script = join(rootDir, 'backend', 'main.py')

  // 先检查后端是否已经在运行
  if (await checkBackend()) {
    console.log('[electron] 后端已在运行，跳过启动')
    return
  }

  if (!existsSync(script)) {
    console.warn('[electron] 后端未随应用打包（backend/main.py 不存在），请先单独启动后端：python backend/main.py')
    return
  }
  if (!existsSync(python)) {
    console.warn(`[electron] 未找到后端 Python（${python}），请先单独启动后端：python backend/main.py`)
    return
  }

  console.log('[electron] 启动后端...')
  backendProcess = spawn(python, [script, '--host', '127.0.0.1', '--port', String(BACKEND_PORT)], {
    cwd: rootDir,
    stdio: 'pipe',
    env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
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
        const http = require('http')
        const req = http.request(
          { host: '127.0.0.1', port: 8801, path: '/api/health/shutdown', method: 'POST', timeout: 500 },
          () => {}
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

function createWindow(): void {
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
  if (process.env.VITE_DEV_SERVER_URL) {
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
  await startBackend()
  createWindow()
  createTray()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
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
