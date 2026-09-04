export type PortraitMood = 'low' | 'plain' | 'lazy' | 'happy' | 'excited'
export type PortraitBond = 'initial' | 'familiar' | 'intimate' | 'lover'

export function portraitMoodFor(value: number): PortraitMood {
  if (value < 25) return 'low'
  if (value < 45) return 'plain'
  if (value < 65) return 'lazy'
  if (value < 85) return 'happy'
  return 'excited'
}

export function portraitBondFor(stage: string): PortraitBond {
  return ({ 初识: 'initial', 熟悉: 'familiar', 亲密: 'intimate', 恋人: 'lover' } as const)[stage as '初识' | '熟悉' | '亲密' | '恋人'] ?? 'initial'
}
