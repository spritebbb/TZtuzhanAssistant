import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { apiFetch } from '../../api'
import RoomPanel from '../RoomPanel.vue'

vi.mock('../../api', () => ({ apiFetch: vi.fn() }))
let data: { items: object[]; preferences: object[]; options: object; enabled: boolean }
beforeEach(() => {
  data = { items: [], preferences: [], options: { color: ['蓝色', '绿色'] }, enabled: true }
  vi.mocked(apiFetch).mockReset().mockImplementation(async () => ({ ok: true, json: async () => JSON.parse(JSON.stringify(data)) }) as Response)
})

it('shows an honest empty room without decorative objects', async () => {
  const wrapper = mount(RoomPanel)
  await flushPromises()
  expect(wrapper.get('[role="img"]').attributes('aria-label')).toBe('房间还是空的')
  expect(wrapper.findAll('.room-object')).toHaveLength(0)
})

it('places, moves within bounds and hides through accessible buttons', async () => {
  const item = { id: 8, title: '故事灯', shape: 'lamp', x: 0, y: .5, placed: false, hidden: false }
  data.items = [item]
  const wrapper = mount(RoomPanel)
  await flushPromises()
  item.placed = true
  await wrapper.get('[aria-label="摆放故事灯"]').trigger('click')
  await flushPromises()
  expect(wrapper.findAll('.room-object')).toHaveLength(1)
  await wrapper.get('[aria-label="左移故事灯"]').trigger('click')
  await flushPromises()
  let args = vi.mocked(apiFetch).mock.calls.filter(c => c[1]?.method === 'PUT').at(-1)!
  expect(args[0]).toBe('/api/memory/aesthetics/placements/8')
  expect(JSON.parse(args[1]!.body as string).x).toBe(0)
  await wrapper.get('[aria-label="隐藏故事灯"]').trigger('click')
  await flushPromises()
  args = vi.mocked(apiFetch).mock.calls.filter(c => c[1]?.method === 'PUT').at(-1)!
  expect(JSON.parse(args[1]!.body as string).hidden).toBe(true)
})

it('keeps owners separate and revokes the selected preference', async () => {
  data.preferences = [{ id: 2, owner: 'user', category: 'color', value: '蓝色' }, { id: 3, owner: 'assistant', category: 'color', value: '绿色' }]
  const wrapper = mount(RoomPanel)
  await flushPromises()
  expect(wrapper.get('[aria-label="你的偏好"]').text()).toContain('蓝色')
  expect(wrapper.get('[aria-label="她的偏好"]').text()).not.toContain('蓝色')
  await wrapper.get('[aria-label="撤销她的绿色偏好"]').trigger('click')
  await flushPromises()
  expect(apiFetch).toHaveBeenCalledWith('/api/memory/aesthetics/3', expect.objectContaining({ method: 'DELETE' }))
})

it('reports load failures without inventing items', async () => {
  vi.mocked(apiFetch).mockResolvedValue({ ok: false } as Response)
  const wrapper = mount(RoomPanel)
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('无法读取')
  expect(wrapper.findAll('.room-object')).toHaveLength(0)
})
