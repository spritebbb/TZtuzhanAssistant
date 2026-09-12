import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getLockStatus,
  initializeKeyslots,
  lockNow,
  unlockLocal,
  unlockRecovery,
} from '../../api/lock'
import LockScreen from '../LockScreen.vue'

vi.mock('../../api/lock', () => ({
  getLockStatus: vi.fn(),
  lockNow: vi.fn(),
  unlockLocal: vi.fn(),
  unlockRecovery: vi.fn(),
  initializeKeyslots: vi.fn(),
}))

const statusInactive = {
  ok: true, state: 'inactive', unlocked: false, key_id: '', locked_at: null,
  slots: { initialized: false, local_slot: false, recovery_slot: false },
  data_encrypted: false,
}

const statusLocked = {
  ok: true, state: 'locked', unlocked: false, key_id: '', locked_at: 1789200000,
  slots: { initialized: true, local_slot: true, recovery_slot: true },
  data_encrypted: false,
}

const statusUnlocked = {
  ok: true, state: 'unlocked', unlocked: true, key_id: 'abcd1234', unlocked_at: 1789200001,
  locked_at: null,
  slots: { initialized: true, local_slot: true, recovery_slot: true },
  data_encrypted: false,
}

describe('LockScreen', () => {
  beforeEach(() => {
    vi.mocked(getLockStatus).mockReset()
    vi.mocked(lockNow).mockReset()
    vi.mocked(unlockLocal).mockReset()
    vi.mocked(unlockRecovery).mockReset()
    vi.mocked(initializeKeyslots).mockReset()
    vi.mocked(getLockStatus).mockResolvedValue(statusLocked as never)
    vi.mocked(lockNow).mockResolvedValue(undefined)
    vi.mocked(unlockLocal).mockResolvedValue(undefined)
    vi.mocked(unlockRecovery).mockResolvedValue(undefined)
    vi.mocked(initializeKeyslots).mockResolvedValue(undefined)
  })

  it('锁定态：本机一键解锁并上报 unlocked', async () => {
    vi.mocked(getLockStatus)
      .mockResolvedValueOnce(statusLocked as never)
      .mockResolvedValueOnce(statusUnlocked as never)
    const wrapper = mount(LockScreen, { props: { personaName: '菟菚' } })
    await flushPromises()

    expect(wrapper.text()).toContain('已锁定')
    await wrapper.get('button.primary').trigger('click')
    await flushPromises()

    expect(unlockLocal).toHaveBeenCalled()
    expect(wrapper.emitted('unlocked')).toBeTruthy()
  })

  it('锁定态：恢复口令错误时如实展示后端统一错误', async () => {
    vi.mocked(unlockRecovery).mockRejectedValueOnce(new Error('解锁失败：口令不正确或密钥槽不可用'))
    const wrapper = mount(LockScreen, { props: {} })
    await flushPromises()

    await wrapper.get('#lock-passphrase').setValue('不对的口令')
    await wrapper.get('.recovery button[type="submit"]').trigger('submit')
    await flushPromises()

    expect(unlockRecovery).toHaveBeenCalledWith('不对的口令')
    expect(wrapper.get('[role="alert"]').text()).toContain('口令不正确')
    expect(wrapper.emitted('unlocked')).toBeFalsy()
  })

  it('未初始化：走创建恢复口令流程，两次不一致被拦在前端', async () => {
    vi.mocked(getLockStatus).mockResolvedValue(statusInactive as never)
    const wrapper = mount(LockScreen, { props: {} })
    await flushPromises()

    expect(wrapper.text()).toContain('还没有启用数据保护')
    await wrapper.get('#init-passphrase').setValue('a-long-passphrase')
    await wrapper.get('#init-passphrase-repeat').setValue('different-repeat')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(initializeKeyslots).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('不一致')

    await wrapper.get('#init-passphrase-repeat').setValue('a-long-passphrase')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(initializeKeyslots).toHaveBeenCalledWith('a-long-passphrase', 'a-long-passphrase')
  })

  it('已解锁：显示如实说明（数据未加密时不冒充磁盘加密）并保留立即锁定入口', async () => {
    vi.mocked(getLockStatus).mockResolvedValue(statusUnlocked as never)
    const wrapper = mount(LockScreen, { props: {} })
    await flushPromises()

    expect(wrapper.text()).toContain('数据加密迁移尚未执行')
    expect(wrapper.text()).not.toContain('数据已加密')
    await wrapper.get('button.ghost').trigger('click')
    await flushPromises()
    expect(lockNow).toHaveBeenCalled()
  })
})
