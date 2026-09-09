import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import { apiFetch } from '../../api'
import TourPanel from '../TourPanel.vue'

vi.mock('../../api', () => ({ apiFetch: vi.fn() }))

const script = {
  ok: true,
  title: '菟菚能力演示',
  intro: '说「新手教程」我就一步步做给你看',
  skill: 'agent-tour',
  steps: [
    {
      id: 'fanout', title: '并行子代理（Agent 核心）', shows: '一次派发多个子代理',
      prompt: '帮我比较 Python 和 Node.js 做后端', tools: ['agent_fanout'],
      check: '结论按维度横向比较',
    },
    {
      id: 'browser', title: '浏览器自动化（MCP）', shows: '真的开浏览器',
      prompt: '用浏览器打开 example.com', tools: ['browser_navigate'],
      check: '弹出浏览器并读出页面内容',
    },
  ],
}

const groupedScript = {
  ok: true,
  title: '菟菚能力演示',
  intro: '说「新手教程」我就一步步做给你看',
  skill: 'agent-tour',
  groups: ['多模态', 'Agent 主秀'],
  steps: [
    {
      id: 'vision', group: '多模态', kind: 'ui', title: '识图（她能看图）',
      shows: '你把图发给她，她能读出画面内容', prompt: '点输入框左侧的图片按钮上传一张图',
      tools: ['/api/vision'], check: '描述的是图里的真实内容',
      needs: '需要配置视觉模型',
    },
    {
      id: 'fanout', group: 'Agent 主秀', kind: 'chat', title: '并行子代理（Agent 核心）',
      shows: '一次派发多个子代理', prompt: '帮我比较 Python 和 Node.js 做后端',
      tools: ['agent_fanout'], check: '结论按维度横向比较',
    },
  ],
}

beforeEach(() => {
  vi.mocked(apiFetch).mockReset().mockImplementation(async () =>
    ({ ok: true, json: async () => JSON.parse(JSON.stringify(script)) }) as Response)
})

it('展示每一步的原话与验收点', async () => {
  const wrapper = mount(TourPanel, { props: { show: false } })
  await wrapper.setProps({ show: true })
  await flushPromises()
  expect(vi.mocked(apiFetch)).toHaveBeenCalledWith('/api/tour')
  expect(wrapper.text()).toContain('并行子代理（Agent 核心）')
  expect(wrapper.text()).toContain('帮我比较 Python 和 Node.js 做后端')
  expect(wrapper.text()).toContain('验收：结论按维度横向比较')
  expect(wrapper.findAll('.step')).toHaveLength(2)
})

it('「发这句」把原话交给上层并关闭面板', async () => {
  const wrapper = mount(TourPanel, { props: { show: false } })
  await wrapper.setProps({ show: true })
  await flushPromises()
  await wrapper.findAll('button.use')[1].trigger('click')
  expect(wrapper.emitted('use')?.[0]).toEqual(['用浏览器打开 example.com'])
  expect(wrapper.emitted('close')).toBeTruthy()
})

it('读取失败时如实提示，不编造步骤', async () => {
  vi.mocked(apiFetch).mockResolvedValue({ ok: false } as Response)
  const wrapper = mount(TourPanel, { props: { show: false } })
  await wrapper.setProps({ show: true })
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('读不到')
  expect(wrapper.findAll('.step')).toHaveLength(0)
})

it('按分组渲染，界面型步骤不给「发这句」而给操作提示', async () => {
  vi.mocked(apiFetch).mockResolvedValue(
    { ok: true, json: async () => JSON.parse(JSON.stringify(groupedScript)) } as Response)
  const wrapper = mount(TourPanel, { props: { show: false } })
  await wrapper.setProps({ show: true })
  await flushPromises()

  expect(wrapper.findAll('.group-title').map(node => node.text())).toEqual(['多模态', 'Agent 主秀'])
  expect(wrapper.findAll('.step')).toHaveLength(2)
  // 只有 chat 型步骤有「发这句」；ui 型步骤给「界面操作」标记
  expect(wrapper.findAll('button.use')).toHaveLength(1)
  expect(wrapper.get('.ui-badge').text()).toBe('界面操作')
  expect(wrapper.text()).toContain('前置条件：需要配置视觉模型')
  expect(wrapper.text()).toContain('共 2 步')
})

it('后端未给 groups 时按步骤分组回退，不丢步骤', async () => {
  vi.mocked(apiFetch).mockResolvedValue({
    ok: true,
    json: async () => ({
      ok: true, title: 'T', intro: '', skill: 'agent-tour',
      steps: [
        { id: 'a', group: '动手与执行', title: 'A', shows: '', prompt: 'p', tools: ['t'], check: 'c' },
        { id: 'b', group: '动手与执行', title: 'B', shows: '', prompt: 'p', tools: ['t'], check: 'c' },
      ],
    }),
  } as Response)
  const wrapper = mount(TourPanel, { props: { show: false } })
  await wrapper.setProps({ show: true })
  await flushPromises()
  expect(wrapper.findAll('.step')).toHaveLength(2)
  expect(wrapper.findAll('.group-title').map(node => node.text())).toEqual(['动手与执行'])
})
