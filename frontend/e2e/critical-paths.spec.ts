import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { execSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
const personaCard = `---
name: Luna E2E
theme: light
subtitle: isolated browser test persona
---

# Luna E2E

You are an isolated test persona.
`

// playwright.config 把本 run 的隔离数据目录写在 .tmp/e2e-datadir（配置文件会被
// 多次求值，指针文件保证多次求值与 spec 读到同一目录）。facts / user_style_map
// 没有生产性 POST 端点（它们是提炼链路的产物），由种子脚本直接写入该隔离
// SQLite；调用时机在 webServer 健康检查之后的用例内，届时表结构已建好。
const projectRoot = resolve(import.meta.dirname, '../..')

function seedE2EData(): void {
  const dataDir = readFileSync(join(projectRoot, '.tmp', 'e2e-datadir'), 'utf-8').trim()
  if (!dataDir) throw new Error('e2e-datadir 为空：种子脚本不知道往哪个隔离目录写入')
  const python = join(projectRoot, '.venv', 'Scripts', 'python.exe')
  execSync(
    `"${python}" "${join(projectRoot, 'scripts', 'e2e_seed.py')}" --data-dir "${dataDir}" --facts 1 --style-map 1`,
    { stdio: 'pipe', env: process.env },
  )
}

async function restoreDefault(request: APIRequestContext) {
  const response = await request.post('/api/personas/default/activate')
  expect(response.ok()).toBeTruthy()
}

async function openApp(page: Page) {
  await page.route(/\/api\/greeting(?:\?.*)?$/, route => route.fulfill({ json: { ok: true, greeting: null } }))
  await page.goto('/')
  await expect(page.getByTitle('切换人格')).toBeVisible()
}

async function openMoreTool(page: Page, name: string | RegExp) {
  await page.getByTitle('更多功能').click()
  const menu = page.getByRole('dialog', { name: '更多功能' })
  await expect(menu).toBeVisible()
  await menu.getByRole('button', { name }).click()
}

test.afterEach(async ({ request }) => {
  // The server runtime is already isolated, but restoring the default persona
  // keeps every scenario independent and makes a failure easier to diagnose.
  await restoreDefault(request)
})

test('opens the application and exposes the companion controls', async ({ page }) => {
  await page.route('**/api/presence', route => route.fulfill({
    json: {
      ok: true,
      visual_state: {
        persona_id: 'default', revision: 1, mood_label: '平静', bond_label: '熟悉',
        energy_band: 'high', activity_kind: 'reading', presence: 'home', quiet: false,
        reduced_motion: false, source_time: '2026-09-10T06:00:00+00:00',
      },
      recent_events: [],
    },
  }))
  await openApp(page)
  await expect(page.getByTitle('一起做点什么')).toBeVisible()
  await page.getByTitle('更多功能').click()
  await expect(page.getByRole('dialog', { name: '更多功能' }).getByRole('button', { name: /成长总览/ })).toBeVisible()
  await expect(page.locator('.presence').filter({ hasText: '陪伴中' })).toBeVisible()
  await expect(page.locator('.portrait-wrap[data-presence="home"]').first()).toBeVisible()
})

test('renders a streamed chat reply without calling a real model', async ({ page }) => {
  await openApp(page)
  await page.route('**/api/chat', async route => {
    await route.fulfill({
      contentType: 'text/event-stream',
      body: 'data: {"piece":"测试回复"}\n\ndata: {"done":"测试回复"}\n\n',
    })
  })

  const input = page.getByPlaceholder(/和.*说点什么/)
  await input.fill('你好，菟菚')
  await input.press('Enter')

  await expect(page.getByText('你好，菟菚', { exact: true })).toBeVisible()
  await expect(page.getByText('测试回复', { exact: true })).toBeVisible()
})

test('opens and closes the shared-activity panel', async ({ page }) => {
  await openApp(page)
  await page.getByTitle('一起做点什么').click()
  await expect(page.getByRole('dialog', { name: '一起做点什么' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: '一起做点什么' })).toBeHidden()
})

test('keeps keyboard focus inside dialogs and returns it to the opener', async ({ page }) => {
  await openApp(page)
  const more = page.getByTitle('更多功能')
  await more.focus()
  await page.keyboard.press('Enter')
  const menu = page.getByRole('dialog', { name: '更多功能' })
  await expect(menu).toBeVisible()
  await expect(menu.getByLabel('查找功能')).toBeFocused()
  const focusStyle = await menu.getByLabel('查找功能').evaluate(element => {
    const style = getComputedStyle(element)
    return { outline: style.outlineStyle, width: Number.parseFloat(style.outlineWidth) }
  })
  expect(focusStyle.outline).not.toBe('none')
  expect(focusStyle.width).toBeGreaterThanOrEqual(2)

  await page.keyboard.press('Shift+Tab')
  await expect(menu.getByRole('button', { name: /重新开始/ })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(menu.getByLabel('查找功能')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(menu).toBeHidden()
  await expect(more).toBeFocused()

  await more.press('Enter')
  await menu.getByRole('button', { name: /设置/ }).click()
  const settings = page.getByRole('dialog', { name: '设置' })
  await expect(settings).toBeVisible()
  await expect(settings.getByRole('button', { name: '关闭设置' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(settings).toBeHidden()
  await expect(more).toBeFocused()
})

test('honors reduced motion and keeps the settings dialog usable at 200 percent scale', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 640, height: 450 })
  await openApp(page)
  await expect.poll(() => page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true)

  await openMoreTool(page, /设置/)
  const settings = page.getByRole('dialog', { name: '设置' })
  await expect(settings).toBeVisible()
  const bounds = await settings.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(640)
  expect(bounds!.y).toBeGreaterThanOrEqual(0)
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(450)
  await expect(settings.getByLabel('对话模型 API 地址')).toBeVisible()
  const duration = await settings.evaluate(element => getComputedStyle(element).animationDuration)
  expect(Number.parseFloat(duration)).toBeLessThanOrEqual(0.00001)
})

test('keeps the settings dialog usable on a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 640 })
  await openApp(page)
  await openMoreTool(page, /设置/)

  const settings = page.getByRole('dialog', { name: '设置' })
  await expect(settings).toBeVisible()
  const bounds = await settings.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(360)
  expect(bounds!.y).toBeGreaterThanOrEqual(0)
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(640)
  await expect(settings.getByLabel('对话模型 API 地址')).toBeVisible()
  expect(await settings.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true)
})

test('shows a sealed future letter without exposing its body', async ({ page, request }) => {
  const secret = 'E2E 密封正文：在未来到来前不该出现在页面里'
  const created = await request.post('/api/future-letters', {
    data: {
      title: '给 2099 年的我们',
      body: secret,
      unlock_type: 'date',
      unlock_at: '2099-01-01T00:00:00',
    },
  })
  expect(created.ok()).toBeTruthy()
  const letterId = (await created.json()).letter.id as number

  await openApp(page)
  await openMoreTool(page, /我们的角落/)
  const dialog = page.getByRole('dialog', { name: '我们的角落' })
  await expect(dialog.getByText('给 2099 年的我们')).toBeVisible()
  await expect(dialog.getByText('封存中')).toBeVisible()
  await expect(dialog.getByText(secret)).toHaveCount(0)

  expect((await request.delete(`/api/future-letters/${letterId}`)).ok()).toBeTruthy()
})

test('shows a dual perspective page with both sides visible', async ({ page, request }) => {
  const created = await request.post('/api/dual-perspectives', {
    data: {
      title: 'E2E 双视角：那场雨',
      source_type: 'free',
      user_view: 'E2E 用户版本：雨里我们只走了五十米。',
      tuzhan_view: 'E2E 菟菚版本：可那五十米我记到现在。',
      tuzhan_view_origin: 'llm',
    },
  })
  expect(created.ok()).toBeTruthy()
  const pageId = (await created.json()).item.id as number

  await openApp(page)
  await openMoreTool(page, /我们的角落/)
  const dialog = page.getByRole('dialog', { name: '我们的角落' })
  await expect(dialog.getByText('E2E 双视角：那场雨')).toBeVisible()
  await expect(dialog.getByText('E2E 用户版本：雨里我们只走了五十米。')).toBeVisible()
  await expect(dialog.getByText('E2E 菟菚版本：可那五十米我记到现在。')).toBeVisible()

  expect((await request.delete(`/api/dual-perspectives/${pageId}`)).ok()).toBeTruthy()
})

test('imports and activates an isolated persona through the real API', async ({ page, request }) => {
  const imported = await request.post('/api/personas/import', {
    multipart: {
      file: {
        name: 'luna-e2e.md',
        mimeType: 'text/markdown',
        buffer: Buffer.from(personaCard),
      },
    },
  })
  expect(imported.ok()).toBeTruthy()
  const body = await imported.json()
  expect(body.persona.name).toBe('Luna E2E')
  expect(body.persona.id).not.toBe('default')

  await openApp(page)
  await expect(page.getByRole('button', { name: /Luna E2E.*PERSONA/ })).toBeVisible()
})

test('starts co-reading, saves a bookmark, and returns discussion text to chat', async ({ page, request }) => {
  await page.route('**/api/activities/*/question', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        question: '第一段说“一起读书”，你觉得两个人一起读和自己读最大的不同是什么？',
      }),
    })
  })

  const upload = await request.post('/api/knowledge/upload', {
    multipart: {
      file: {
        name: '一起读的测试.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from('第一段：一起读书。\n\n第二段：留下书签。'),
      },
    },
  })
  expect(upload.ok()).toBeTruthy()

  await openApp(page)
  await page.getByTitle('一起做点什么').click()
  await page.getByRole('button', { name: /一起读的测试\.txt/ }).click()
  await expect(page.getByText('第 1 / 1 段')).toBeVisible()

  await page.getByPlaceholder('写下一句想法，下次回来还在这里').fill('这里值得慢一点读。')
  await page.getByRole('button', { name: '收进书签' }).click()
  await expect(page.getByText('这张书签夹好了')).toBeVisible()

  await page.getByRole('button', { name: /让.*问个具体问题/ }).click()
  await expect(page.getByPlaceholder(/和.*说点什么/)).toHaveValue(
    /关于《一起读的测试\.txt》第 1 段，你问我：“第一段说“一起读书”/,
  )
})

test('toggles a feature switch in settings and sees the change persisted', async ({ page }) => {
  await openApp(page)
  await openMoreTool(page, /设置/)
  const settings = page.getByRole('dialog', { name: '设置' })
  await expect(settings).toBeVisible()

  // 功能开关面板从 /api/flags 拉取，改动即时落盘（无需点保存）。
  // 选「表达习惯观察」（style_map_enabled）：真实消费方在 pipeline 注入路径。
  const flagToggle = settings.getByRole('checkbox', { name: /表达习惯观察/ })
  await expect(flagToggle).toBeVisible()
  const before = await flagToggle.isChecked()
  await flagToggle.click()
  await expect(settings.getByRole('status')).toContainText('已保存并立即生效')

  // 后端确认落盘（切换后与切换前互补）
  const flags = await (await page.request.get('/api/flags')).json()
  expect(flags.flags.style_map_enabled).toBe(!before)

  // 还原，避免影响同套件后续用例的注入行为
  await flagToggle.click()
  const flagsAfter = await (await page.request.get('/api/flags')).json()
  expect(flagsAfter.flags.style_map_enabled).toBe(before)
})

test('edits, pins and forgets a memory through the real API roundtrip', async ({ page }) => {
  seedE2EData()
  await openApp(page)
  await openMoreTool(page, /记忆与了解她/)
  const memory = page.getByRole('dialog', { name: /记住的事/ })
  await expect(memory).toBeVisible()

  const original = 'E2E 用户住在江城，喜欢雨天散步。'
  const edited = 'E2E 用户住在江城，最喜欢下雨天的江滩。'
  await expect(memory.getByText(original)).toBeVisible()
  // 定位卡片用「预设事实的容器」——进入编辑态后正文进入 textarea，hasText 不再命中
  const before = memory.locator('article', { hasText: original })
  await expect(before).toHaveCount(1)

  // 改写 → 保存后来源变「你已确认」、置信度变 100%
  await before.getByRole('button', { name: '改写' }).click()
  await memory.locator('article textarea').fill(edited)
  await memory.getByRole('button', { name: '保存' }).click()
  await expect(memory.getByText(edited)).toBeVisible()

  const card = memory.locator('article', { hasText: edited })
  await expect(card.getByText('你已确认')).toBeVisible()
  await expect(card.getByText('置信度 100%')).toBeVisible()

  // 固定 → 复选勾上且 provenance 出现「长期保留」
  const pinToggle = card.getByRole('checkbox', { name: '长期保留' })
  await pinToggle.click()
  await expect(pinToggle).toBeChecked()
  await expect(card.locator('.provenance')).toContainText('长期保留')

  // 忘掉（window.confirm 两段式）→ 条目消失
  await page.evaluate(() => { window.confirm = () => true })
  await card.getByRole('button', { name: '忘掉' }).click()
  await expect(memory.getByText(edited)).toHaveCount(0)
  await expect(memory.getByText(original)).toHaveCount(0)
})

test('feeds a document to the bookshelf and removes it again', async ({ page }) => {
  await openApp(page)
  await openMoreTool(page, /书架/)
  const shelf = page.getByRole('dialog', { name: /的书架/ })
  await expect(shelf).toBeVisible()

  // 走真实上传端点（与共读用例同管道，但这里验证书架面板自身的增删链路）
  const fileInput = shelf.locator('input[type="file"]')
  await fileInput.setInputFiles({
    name: 'E2E 投喂笔记.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from('# E2E 投喂\n\n这是一段给书架的测试内容，用来验证上传与删除。'),
  })
  // 通知文案里也含文件名（《…》读完了），故以 .doc-row 为准，避免 strict 冲突
  const row = shelf.locator('.doc-row', { hasText: 'E2E 投喂笔记.md' })
  await expect(row).toBeVisible({ timeout: 15_000 })
  await expect(row.locator('.sub')).toContainText('段')

  // 拿掉 → 文档行消失（无确认弹窗，删除即生效）
  await row.getByRole('button', { name: '拿掉' }).click()
  await expect(row).toHaveCount(0)
})

test('shows observed expression habits and lets the user delete one', async ({ page }) => {
  seedE2EData()
  await openApp(page)
  await openMoreTool(page, /记忆与了解她/)
  const memory = page.getByRole('dialog', { name: /记住的事/ })
  await memory.getByRole('button', { name: /了解/ }).click()

  // 「了解她」tab：场景化表达观察区展示种子数据（≥2 次的场景）
  const habitsCard = memory.locator('article.profile-card', { hasText: '她观察到你说话的习惯' })
  await expect(habitsCard.getByText(/倾诉烦恼时——E2E 喜欢用短句加省略号/)).toBeVisible()
  // 只被观察到 1 次的场景同样展示（主权在用户，不在此处过滤）
  await expect(habitsCard.getByText(/聊到晚饭时——E2E 会先报菜名再说吃过了/)).toBeVisible()

  // 逐条删除（真实 DELETE /api/memory/style-map/{id}）
  await habitsCard.getByRole('button', { name: /删掉这条观察/ }).first().click()
  await expect(habitsCard.getByText(/倾诉烦恼时——E2E 喜欢用短句加省略号/)).toHaveCount(0)
})

test('locks the app and unlocks via the local slot through the real lock screen', async ({ page }) => {
  // 放最后执行：初始化密钥槽后同 run 内其他用例依赖解锁态（本用例以解锁收尾）
  await openApp(page)
  // 未初始化：无锁定按钮（inactive 不显示入口）
  await expect(page.getByTitle('锁定应用（忘掉密钥，需解锁后继续）')).toHaveCount(0)

  const init = await page.request.post('/api/lock/initialize', {
    headers: { 'Content-Type': 'application/json' },
    data: { passphrase: 'e2e-lock-passphrase', passphrase_repeat: 'e2e-lock-passphrase' },
  })
  expect(init.ok()).toBeTruthy()

  // 刷新后：已初始化 → 头部出现锁定按钮
  await page.reload()
  await expect(page.getByTitle('切换人格')).toBeVisible()
  const lockBtn = page.getByTitle('锁定应用（忘掉密钥，需解锁后继续）')
  await expect(lockBtn).toBeVisible()

  await lockBtn.click()
  const lockScreen = page.getByRole('dialog', { name: '应用锁定' })
  await expect(lockScreen).toBeVisible()
  await expect(lockScreen.getByText('已锁定')).toBeVisible()
  // 锁定态：普通 API 被拦（423）
  expect((await page.request.get('/api/meta')).status()).toBe(423)

  await lockScreen.getByRole('button', { name: '本机解锁' }).click()
  await expect(lockScreen).toHaveCount(0)
  await expect(page.getByTitle('一起做点什么')).toBeVisible()
  expect((await page.request.get('/api/meta')).status()).toBe(200)
})
