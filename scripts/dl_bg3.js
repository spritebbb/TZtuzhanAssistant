// 用 Unsplash source 服务按关键词拿植物背景图（可复现性差但主题可控）
const fs = require('fs')
const path = require('path')
const OUT_DIR = 'D:/DSH/TZtuzhanAssistant/assets'

// source.unsplash.com 已停用；改用 Pexels API 无 key 不行。改试 pixabay CDN 直达 / unsplash feature
// 这里用 loremflickr 作植物关键词下载（免费，按关键词给图）
const queries = [
  { name: 'p1_dark_leaves', q: 'dark,leaves', w: 2400, h: 1600 },
  { name: 'p2_green_plant', q: 'green,plant', w: 2400, h: 1600 },
  { name: 'p3_vine', q: 'vine,green', w: 2400, h: 1600 },
  { name: 'p4_botanical', q: 'botanical,plant', w: 2400, h: 1600 },
  { name: 'p5_fern', q: 'fern,plant', w: 2400, h: 1600 },
  { name: 'p6_tropical_leaf', q: 'tropical,leaf', w: 2400, h: 1600 },
]

async function dl() {
  for (const c of queries) {
    const url = `https://loremflickr.com/${c.w}/${c.h}/${c.q}`
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(20000), redirect: 'follow' })
      const ct = r.headers.get('content-type') || ''
      if (!r.ok || !ct.includes('image')) { console.log(`[skip] ${c.name}: ${r.status} ${ct}`); continue }
      const b = Buffer.from(await r.arrayBuffer())
      const out = path.join(OUT_DIR, c.name + '.jpg')
      fs.writeFileSync(out, b)
      console.log(`[OK] ${c.name}: ${b.length} -> ${out}`)
    } catch (e) { console.log(`[skip] ${c.name}: ${e.message}`) }
  }
}
dl()
