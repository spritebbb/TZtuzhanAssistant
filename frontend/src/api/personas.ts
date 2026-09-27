import { apiFetch } from './index'

export interface PersonaProfile {
  id: string
  name: string
  subtitle: string
  theme: 'dark' | 'light'
  voice: string
  motion_enabled?: boolean
  active: boolean
  created_at: number
}

export async function listPersonas(): Promise<{ active: PersonaProfile; personas: PersonaProfile[] }> {
  const response = await apiFetch('/api/personas')
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '读取人格列表失败')
  return { active: data.active, personas: data.personas || [] }
}

export async function activatePersona(id: string): Promise<PersonaProfile> {
  const response = await apiFetch(`/api/personas/${encodeURIComponent(id)}/activate`, { method: 'POST' })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '切换人格失败')
  return data.persona
}

export async function importPersona(file: File): Promise<PersonaProfile> {
  const form = new FormData()
  form.append('file', file)
  const response = await apiFetch('/api/personas/import', { method: 'POST', body: form })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '导入人格卡失败')
  return data.persona
}

/** ST 卡导入向导：卡 → 菟菚人格卡 markdown 预览（不入库，用户确认后走 importPersona）。 */
export async function convertStCard(file: File): Promise<{ markdown: string; name: string; warnings: string[] }> {
  const form = new FormData()
  form.append('file', file)
  const response = await apiFetch('/api/personas/convert-stcard', { method: 'POST', body: form })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || 'ST 卡转换失败')
  return { markdown: String(data.markdown || ''), name: String(data.name || ''), warnings: Array.isArray(data.warnings) ? data.warnings : [] }
}

export async function generatePersona(brief: string): Promise<{ markdown: string; name: string }> {
  const response = await apiFetch('/api/personas/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ brief }),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '生成人格卡失败')
  return { markdown: data.markdown, name: data.name }
}

export async function updatePersona(id: string, updates: Partial<PersonaProfile>): Promise<PersonaProfile> {
  const response = await apiFetch(`/api/personas/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  })
  const data = await response.json()
  if (!response.ok || !data.ok) throw new Error(data.error || '保存人格设置失败')
  return data.persona
}
