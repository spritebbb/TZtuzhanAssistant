import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ChatInput from '../ChatInput.vue'


describe('ChatInput privacy mode', () => {
  it('exposes a one-turn no-trace switch', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        input: '',
        ephemeral: false,
        busy: false,
        streaming: false,
      },
    })

    await wrapper.get('[title="快捷指令"]').trigger('click')
    const privacy = wrapper.get('.privacy-toggle')
    expect(privacy.attributes('aria-pressed')).toBe('false')
    await privacy.trigger('click')
    expect(wrapper.emitted('update:ephemeral')?.[0]).toEqual([true])
  })

  it('disables image upload while no-trace mode is active', async () => {
    const wrapper = mount(ChatInput, {
      props: {
        input: '',
        ephemeral: true,
        busy: false,
        streaming: false,
      },
    })

    const imageButton = wrapper.findAll('.icon-btn')[1]
    expect(imageButton.attributes('disabled')).toBeDefined()
    await wrapper.get('[title="快捷指令"]').trigger('click')
    expect(wrapper.get('.privacy-toggle').text()).toContain('已开启')
  })
})
