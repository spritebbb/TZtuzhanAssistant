import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiFetch } from '../../api'
import AgentPanel from '../AgentPanel.vue'

vi.mock('../../api', () => ({ apiFetch: vi.fn() }))

const mockedApiFetch = vi.mocked(apiFetch)

function response(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

describe('AgentPanel', () => {
  beforeEach(() => {
    mockedApiFetch.mockReset()
    mockedApiFetch.mockImplementation(async (path) => {
      if (path === '/api/agent/tasks') {
        return response({
          ok: true,
          tasks: [
            { id: 'running', objective: 'running task', status: 'running' },
            { id: 'planned', objective: 'planned task', status: 'planned' },
          ],
        })
      }
      if (path === '/api/agent/tasks/running') {
        return response({
          ok: true,
          task: { id: 'running', objective: 'running task', status: 'running' },
        })
      }
      if (path === '/api/agent/tasks/planned') {
        return response({
          ok: true,
          task: {
            id: 'planned',
            objective: 'planned task',
            status: 'planned',
            plan: [],
            step_confirmations: {},
          },
        })
      }
      throw new Error(`unexpected request: ${path}`)
    })
  })

  it('clears running state when selecting a non-running task', async () => {
    const wrapper = mount(AgentPanel, {
      props: { show: false },
      global: { stubs: { Teleport: true } },
    })

    await wrapper.setProps({ show: true })
    await flushPromises()
    const tasks = wrapper.findAll('.a-item')

    await tasks[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.a-btn.cancel').exists()).toBe(true)

    await tasks[1].trigger('click')
    await flushPromises()
    expect(wrapper.find('.a-btn.cancel').exists()).toBe(false)
    expect(wrapper.find('.a-btn.run').attributes('disabled')).toBeUndefined()
  })
})
