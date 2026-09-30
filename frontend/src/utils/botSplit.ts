// 菟菚回复按行拆条显示（2026-09-30 用户拍板方案 A：显示层拆条，数据层仍是一条消息）。
// 人格卡第 56 行「像网友在 QQ 发消息……用换行把一段话拆成几小截发出」——模型
// 一直照此输出多行短句，但聊天视图把整段渲染成单气泡，用户看到的是「一句话
// 发送完」。本模块把提示词承诺的视觉语义在显示层兑现：多行 bot 文本按行拆成
// 多个气泡。拆出的行打 continuation 标记，与承载原始字段（图片/解释/草稿）的
// 数据消息区分；会话数据、导出、后端锚定（reply_quality 锚「最近 assistant 行」）
// 都不受影响。

import type { Message } from '../api/sessions'

// 拆条上限：人格卡口径「接得住就一句，铺开两三截」，偶尔一口气一小段；超过
// 这个行数的回复更像长文，拆成气泡串反而刷屏，整段单气泡更可读。
const MAX_SPLIT_LINES = 8

/**
 * 按行拆 bot 文本：空行跳过、行首尾去空白。
 * 返回 null 表示不可拆/不值得拆（单行、纯空白、含代码块、行数超上限）。
 * 代码块内的换行是格式的一部分（fence 内一行一行），按行拆会把代码块拆散，
 * 整体不拆保守处理。
 */
export function splitBotText(text: string): string[] | null {
  const raw = (text || '').replace(/\r\n?/g, '\n')
  if (!raw.trim() || raw.includes('```')) return null
  const lines = raw.split('\n').map((l) => l.trim()).filter((l) => l)
  if (lines.length < 2 || lines.length > MAX_SPLIT_LINES) return null
  return lines
}

/**
 * 把一条 bot Message 拆成显示用消息数组：前面的行是续条（continuation: true，
 * 只带文本），最后一行落在原消息的浅拷贝上（保留 image/explanation/draft 等
 * 原始字段——操作入口集中在回复末尾）。user 消息与不可拆文本原样返回。
 */
export function splitMessageForDisplay(msg: Message): Message[] {
  if (msg.role !== 'bot') return [msg]
  const lines = splitBotText(msg.content || '')
  if (!lines) return [msg]
  const parts: Message[] = lines.slice(0, -1).map((line) => ({
    role: 'bot' as const,
    content: line,
    ephemeral: msg.ephemeral,
    ts: msg.ts,
    continuation: true,
  }))
  return [...parts, { ...msg, content: lines[lines.length - 1] }]
}
