// 从插画站抓取植物藤蔓背景图的可用图片直链，并下载
const fs = require('fs')
const path = require('path')

const OUT_DIR = 'D:/DSH/TZtuzhanAssistant/assets'

// 插画站搜索页（尝试抓 img 直链）
const pages = [
  { name: 'vecteezy_vine', url: 'https://www.vecteezy.com/free-png/green-vine' },
  { name: 'favpng_vine', url: 'https://favpng.com/png_search/botanical-vines' },
  { name: 'imgbin_vine', url: 'https://imgbin.com/free-png/botanical-vines' },
  { name: 'pikwizard_vine', url: 'https://pikwizard.com/s/png/green-vines/' },
]

async function probe(p) {
  try {
    const r = await fetch(p.url, {
      headers: { 'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'accept': 'text/html,image/avif,image/webp,*/*' },
      signal: AbortSignal.timeout(15000),
    })
    const html = await r.text()
    // 抓取所有图片直链（jpg/png/webp，排除 dataURI）
    const urls = new Set()
    const re = /https?:\/\/[^'"\s]+?\.(?:png|jpg|jpeg|webp)(?:\?[^'"\s]*)?/gi
    let m
    while ((m = re.exec(html))) urls.add(m[0])
    console.log(`[${p.name}] HTTP ${r.status}, ${html.length}b, img urls: ${urls.size}`)
    return Array.from(urls).slice(0, 12)
  } catch (e) {
    console.log(`[${p.name}] ERR ${e.message}`)
    return []
  }
}

async function downloadImg(url, name) {
  try {
    const r = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' }, signal: AbortSignal.timeout(20000) })
    const ct = r.headers.get('content-type') || ''
    if (!r.ok || !ct.includes('image')) { console.log(`  [skip] ${name}: ${r.status} ${ct}`); return }
    const b = Buffer.from(await r.arrayBuffer())
    const ext = ct.includes('png') ? 'png' : ct.includes('webp') ? 'webp' : 'jpg'
    const out = path.join(OUT_DIR, `${name}.${ext}`)
    fs.writeFileSync(out, b)
    console.log(`  [OK] ${name}: ${b.length} -> ${out}`)
  } catch (e) { console.log(`  [skip] ${name}: ${e.message}`) }
}

async function main() {
  for (const p of pages) {
    const urls = await probe(p)
    for (let i = 0; i < Math.min(urls.length, 4); i++) {
      await downloadImg(urls[i], `${p.name}_${i}`)
    }
  }
}
main()
