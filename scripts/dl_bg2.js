// 探测更多暗色藤蔓/植物背景，下载可用的
const fs = require('fs')
const path = require('path')

const OUT_DIR = 'D:/DSH/TZtuzhanAssistant/assets'

const candidates = [
  // 暗色植物/叶子特写（偏装饰、淡雅）
  { name: 'b1_dark_leaf', url: 'https://images.unsplash.com/photo-1501004318641-b39e6451bec6?w=2400&q=80' },
  { name: 'b2_tropical_dark', url: 'https://images.unsplash.com/photo-1512428559087-560fa5ceab42?w=2400&q=80' },
  { name: 'b3_monstera', url: 'https://images.unsplash.com/photo-1509937528035-ad76254b0356?w=2400&q=80' },
  { name: 'b4_green_plant_bg', url: 'https://images.unsplash.com/photo-1502104034360-73176f5f4d29?w=2400&q=80' },
  { name: 'b5_fern_forest', url: 'https://images.unsplash.com/photo-1518495973542-4542c06a5843?w=2400&q=80' },  // 暗光森林光斑
  { name: 'b6_dark_leaves', url: 'https://images.unsplash.com/photo-1509316785289-025f5b846b35?w=2400&q=80' },  // 叶脉
  { name: 'b7_venation', url: 'https://images.unsplash.com/photo-1550963295-019d8a8a61c5?w=2400&q=80' },
  { name: 'b8_greenery', url: 'https://images.unsplash.com/photo-1416879595882-3373a0480b5b?w=2400&q=80' },  // 绿植盆栽
  { name: 'b9_ivy_vine', url: 'https://images.unsplash.com/photo-1507238691740-187a5b1d37b8?w=2400&q=80' },  // 藤蔓爬墙
  { name: 'b10_vine_dark', url: 'https://images.unsplash.com/photo-1458609267953-5c7c1a9c9e3f?w=2400&q=80' },
]

async function download() {
  let n = 0
  for (const c of candidates) {
    try {
      const r = await fetch(c.url, { headers: { 'user-agent': 'Mozilla/5.0' }, signal: AbortSignal.timeout(15000) })
      const ct = r.headers.get('content-type') || ''
      if (!r.ok || !ct.includes('image')) { console.log(`[skip] ${c.name}: ${r.status} ${ct}`); continue }
      const b = Buffer.from(await r.arrayBuffer())
      const ext = ct.includes('png') ? 'png' : ct.includes('webp') ? 'webp' : 'jpg'
      const out = path.join(OUT_DIR, c.name + '.' + ext)
      fs.writeFileSync(out, b)
      console.log(`[OK] ${c.name}: ${b.length} -> ${out}`)
      n++
    } catch (e) { console.log(`[skip] ${c.name}: ${e.message}`) }
  }
  console.log(`[DONE] downloaded ${n}`)
}
download()
