// L16 可选择共享：默认一切隔离，只有用户明确「分享给某角色」才建立授权（首期只读）。
import { apiFetch } from './index'

export interface ShareGrant {
  grantee: string
  permission: string
  revoked: boolean
}

export interface ShareItem {
  id: number
  resource_type: string
  resource_id: number
  version: number
  revoked: boolean
  grants: ShareGrant[]
}

export async function listShares(): Promise<ShareItem[]> {
  const response = await apiFetch('/api/shared')
  if (!response.ok) throw new Error('共享清单读取失败')
  const data = await response.json()
  return (data.shares ?? []) as ShareItem[]
}

export async function shareResource(
  resourceType: string,
  resourceId: number,
  granteePersona: string,
): Promise<boolean> {
  const response = await apiFetch('/api/shared', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      resource_type: resourceType,
      resource_id: resourceId,
      grantee_persona: granteePersona,
    }),
  })
  return response.ok
}

export async function revokeShare(
  resourceType: string,
  resourceId: number,
  granteePersona?: string,
): Promise<boolean> {
  const suffix = granteePersona ? `?grantee=${encodeURIComponent(granteePersona)}` : ''
  const response = await apiFetch(
    `/api/shared/${resourceType}/${resourceId}${suffix}`,
    { method: 'DELETE' },
  )
  return response.ok
}
