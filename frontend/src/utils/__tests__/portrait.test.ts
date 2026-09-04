import { describe, expect, it } from 'vitest'
import { portraitBondFor, portraitMoodFor } from '../portrait'

describe('portrait state mapping', () => {
  it('maps the five mood bands at exact boundaries', () => {
    expect([0, 24, 25, 44, 45, 64, 65, 84, 85, 100].map(portraitMoodFor)).toEqual([
      'low', 'low', 'plain', 'plain', 'lazy', 'lazy', 'happy', 'happy', 'excited', 'excited',
    ])
  })

  it('maps relationship stages and falls back safely', () => {
    expect(['初识', '熟悉', '亲密', '恋人', '未知'].map(portraitBondFor)).toEqual([
      'initial', 'familiar', 'intimate', 'lover', 'initial',
    ])
  })
})
