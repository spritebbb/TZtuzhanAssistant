import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const personaCard = `---
name: Luna E2E
theme: light
subtitle: isolated browser test persona
---

# Luna E2E

You are an isolated test persona.
`

async function restoreDefault(request: APIRequestContext) {
  const response = await request.post('/api/personas/default/activate')
  expect(response.ok()).toBeTruthy()
}

async function openApp(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('button', { name: /菟菚.*PERSONA/ })).toBeVisible()
}

test.afterEach(async ({ request }) => {
  // The server runtime is already isolated, but restoring the default persona
  // keeps every scenario independent and makes a failure easier to diagnose.
  await restoreDefault(request)
})

test('opens the application and exposes the companion controls', async ({ page }) => {
  await openApp(page)
  await expect(page.getByTitle('一起做点什么')).toBeVisible()
  await expect(page.getByTitle('成长总览')).toBeVisible()
  await expect(page.getByText('陪伴中')).toBeVisible()
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
  await page.getByTitle('我们的角落（一起留下的东西）').click()
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
  await page.getByTitle('我们的角落（一起留下的东西）').click()
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

  await page.goto('/')
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
