// botSplit（方案 A 显示层拆条，2026-09-30 用户拍板）：
// - splitBotText：按行拆、空行跳过、行 trim；代码块/单行/超上限不拆（返回 null）
// - splitMessageForDisplay：bot 消息拆成「续条…+组尾原字段」结构，user/不可拆原样
// 对应用户报告「菟菚说话分开发送有问题」——人格卡 56 行承诺的「一截一截发出」
// 此前只有提示词没有显示层实现（数据库 messages 表从未有连续两条 assistant 行）。
import { describe, expect, it } from 'vitest'
import { splitBotText, splitMessageForDisplay } from '../botSplit'
import type { Message } from '../../api/sessions'

describe('splitBotText', () => {
  it('多行文本按行拆开，空行跳过、行首尾空白去掉', () => {
    expect(splitBotText('嗯\n你先忙吧\n回头聊')).toEqual(['嗯', '你先忙吧', '回头聊'])
    expect(splitBotText('你还真折腾这个\n\n升级完我哪里不一样了，说来听听'))
      .toEqual(['你还真折腾这个', '升级完我哪里不一样了，说来听听'])
    expect(splitBotText('  前面带空格 \n\t后面带 tab\t ')).toEqual(['前面带空格', '后面带 tab'])
  })

  it('CRLF 归一为 LF 再拆', () => {
    expect(splitBotText('一句\r\n另一句\r\n')).toEqual(['一句', '另一句'])
  })

  it('单行/纯空白/空文本不拆（null）', () => {
    expect(splitBotText('就一句话')).toBeNull()
    expect(splitBotText('')).toBeNull()
    expect(splitBotText('  \n \n \t ')).toBeNull()
  })

  it('含代码块不拆：fence 内换行是格式的一部分', () => {
    expect(splitBotText('第一行\n```python\nprint(1)\nprint(2)\n```\n收尾')).toBeNull()
  })

  it('超过上限行数不拆：长文拆成气泡串反而刷屏', () => {
    const nine = Array.from({ length: 9 }, (_, i) => `第${i + 1}行`).join('\n')
    expect(splitBotText(nine)).toBeNull()
    const eight = Array.from({ length: 8 }, (_, i) => `第${i + 1}行`).join('\n')
    expect(splitBotText(eight)).toHaveLength(8)
  })
})

describe('splitMessageForDisplay', () => {
  const base: Message = { role: 'bot', content: '', ts: 1700000000 }

  it('多行 bot 消息：前行为续条，末行落在保留原字段的组尾', () => {
    const msg: Message = { ...base, content: '你还真折腾这个\n\n升级完我哪里不一样了', image: '/a.png', ephemeral: true }
    const parts = splitMessageForDisplay(msg)
    expect(parts).toHaveLength(2)
    expect(parts[0]).toMatchObject({ role: 'bot', content: '你还真折腾这个', continuation: true, ephemeral: true, ts: msg.ts })
    expect(parts[0].image).toBeUndefined()
    expect(parts[1]).toMatchObject({ content: '升级完我哪里不一样了', image: '/a.png', ephemeral: true })
    expect(parts[1].continuation).toBeUndefined()
  })

  it('单行/代码块/超上限 bot 消息原样返回（单元素数组）', () => {
    expect(splitMessageForDisplay({ ...base, content: '一句话' })).toHaveLength(1)
    expect(splitMessageForDisplay({ ...base, content: 'a\n```\nb\n```' })).toHaveLength(1)
  })

  it('user 消息永不拆', () => {
    const user: Message = { role: 'user', content: '第一行\n第二行', ts: 1 }
    const parts = splitMessageForDisplay(user)
    expect(parts).toEqual([user])
  })
})
