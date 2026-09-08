import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { apiFetch } from '../../api'
import KnowledgePanel from '../KnowledgePanel.vue'

vi.mock('../../api', () => ({ apiFetch: vi.fn() }))

const doc = { id: 5, filename: '共读书.pdf', format: 'pdf', size_bytes: 1024, chunk_count: 3, ts: '2026-09-09T10:00:00' }
let shares: object[] = []

beforeEach(() => {
  shares = []
  vi.mocked(apiFetch).mockReset().mockImplementation(async (path: string, init?: RequestInit) => {
    const url = String(path)
    if (url.startsWith('/api/knowledge/documents')) {
      return { ok: true, json: async () => ({ documents: [doc] }) } as Response
    }
    if (url.startsWith('/api/knowledge/opinions')) {
      return { ok: true, json: async () => ({ opinions: [] }) } as Response
    }
    if (url.startsWith('/api/personas')) {
      return {
        ok: true,
        json: async () => ({
          ok: true,
          active: { id: 'default', name: '菟菚' },
          personas: [{ id: 'default', name: '菟菚' }, { id: 'p2', name: '乙' }],
        }),
      } as Response
    }
    if (url.startsWith('/api/shared') && (!init?.method || init.method === 'GET')) {
      return { ok: true, json: async () => ({ shares }) } as Response
    }
    return { ok: true, json: async () => ({ ok: true }) } as Response
  })
})

it('默认不展示共享目标，只有明确点开才列出其他角色', async () => {
  const wrapper = mount(KnowledgePanel, { props: { show: false } })
  await wrapper.setProps({ show: true })
  await flushPromises()
  expect(wrapper.text()).toContain('共读书.pdf')
  expect(wrapper.text()).not.toContain('分享给 乙')
  await wrapper.get('button[title="分享给其他角色"]').trigger('click')
  await flushPromises()
  expect(vi.mocked(apiFetch).mock.calls.some(c => String(c[0]).startsWith('/api/personas'))).toBe(true)
})

it('分享走 grantee_persona，撤销走 DELETE 带 grantee', async () => {
  const wrapper = mount(KnowledgePanel, { props: { show: false } })
  await wrapper.setProps({ show: true })
  await flushPromises()
  await wrapper.get('button[title="分享给其他角色"]').trigger('click')
  await flushPromises()
  await wrapper.get('button[aria-label="分享给乙"]').trigger('click')
  await flushPromises()
  const shareCall = vi.mocked(apiFetch).mock.calls.find(
    c => String(c[0]) === '/api/shared' && c[1]?.method === 'POST',
  )
  expect(shareCall).toBeTruthy()
  expect(JSON.parse(shareCall![1]!.body as string)).toMatchObject({
    resource_type: 'kb_document', resource_id: 5, grantee_persona: 'p2',
  })

  // 授权生效后按钮变为「取消分享」
  shares = [{
    id: 1, resource_type: 'kb_document', resource_id: 5, version: 1, revoked: false,
    grants: [{ grantee: 'assistant-main::persona::p2', permission: 'read', revoked: false }],
  }]
  await wrapper.get('button[title="分享给其他角色"]').trigger('click')  // 收起
  await flushPromises()
  await wrapper.get('button[title="分享给其他角色"]').trigger('click')  // 重新展开 → 重读授权
  await flushPromises()
  await wrapper.get('button[aria-label="取消分享给乙"]').trigger('click')
  await flushPromises()
  const revokeCall = vi.mocked(apiFetch).mock.calls.find(c => c[1]?.method === 'DELETE')
  expect(revokeCall).toBeTruthy()
  expect(String(revokeCall![0])).toBe('/api/shared/kb_document/5?grantee=p2')
})
