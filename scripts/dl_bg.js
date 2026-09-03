// 探测多个候选背景图直链，下载可用的第一张
const fs = require('fs')
const path = require('path')

const OUT_DIR = 'D:/DSH/TZtuzhanAssistant/assets'

// 候选：优先直接的图片直链
const candidates = [
  { name: 'c1_unsplash_vine', url: 'https://images.unsplash.com/photo-1466692476868-aef1dfb1e735?w=2400&q=80' },          // 绿植
  { name: 'c2_unsplash_leaves', url: 'https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=2400&q=80' },      // 森林绿植
  { name: 'c3_unsplash_darkplant', url: 'https://images.unsplash.com/photo-1518531933037-91b2f5f229cc?w=2400&q=80' },   // 暗色叶子
  { name: 'c4_unsplash_fern', url: 'https://images.unsplash.com/photo-1448375240586-882707db888b?w=2400&q=80' },         // 蕨类森林
  { name: 'c5_unsplash_jungle', url: 'https://images.unsplash.com/photo-1502082553048-f009c37129b9?w=2400&q=80' },       // 绿植
  { name: 'c6_unsplash_monstera_dark', url: 'https://images.unsplash.com/photo-1614594975525-e45190c55d0b?w=2400&q=80' }, // 暗色龟背竹
  // Unsplash 指定照片 ID 的 source 方式
  { name: 'c7_unsplash_vine_soil', url: 'https://images.unsplash.com/photo-1458609267953-5c7c1a9c9e3f?w=2400&q=80' },
]

async function download() {
  for (const c of candidates) {
    try {
      const r = await fetch(c.url, { headers: { 'user-agent': 'Mozilla/5.0' }, signal: AbortSignal.timeout(15000) })
      const ct = r.headers.get('content-type') || ''
      if (!r.ok) { console.log(`[skip] ${c.name}: HTTP ${r.status} (${ct})`); continue }
      if (!ct.includes('image')) { console.log(`[skip] ${c.name}: not image (${ct})`); continue }
      const b = Buffer.from(await r.arrayBuffer())
      const ext = ct.includes('png') ? 'png' : ct.includes('webp') ? 'webp' : 'jpg'
      const out = path.join(OUT_DIR, c.name + '.' + ext)
      fs.writeFileSync(out, b)
      console.log(`[OK] ${c.name}: ${b.length} bytes -> ${out}`)
      return out
    } catch (e) {
      console.log(`[skip] ${c.name}: ${e.message}`)
    }
  }
  console.log('[DONE] no image downloaded')
  return null
}

download()
