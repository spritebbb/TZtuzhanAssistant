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

  await page.getByRole('button', { name: /去和.*聊这一段/ }).click()
  await expect(page.getByPlaceholder(/和.*说点什么/)).toHaveValue(/我们继续共读《一起读的测试\.txt》吧/)
})
