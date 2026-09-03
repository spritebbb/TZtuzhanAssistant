// 从 vecteezy 抓植物背景类大图（深色/可铺满），挑分辨率较大的
const fs = require('fs')
const path = require('path')

const OUT_DIR = 'D:/DSH/TZtuzhanAssistant/assets'
const UA = { 'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 'accept': 'text/html,image/*' }

const searches = [
  { name: 'vg_darkplant', url: 'https://www.vecteezy.com/free-vector/dark-plant-background' },
  { name: 'vg_botanical', url: 'https://www.vecteezy.com/free-vector/botanical-background' },
  { name: 'vg_vine', url: 'https://www.vecteezy.com/free-vector/green-vine-background' },
]

function extractUrls(html, minW = 600) {
  const urls = new Set()
  // vecteezy 图片多在 img 标签 srcset 或 cdn
  const re = /(https?:\/\/[^\s"'<>]+?\.(?:png|jpg|jpeg|webp)(?:\?[^\s"'<>]*)?)/gi
  let m
  while ((m = re.exec(html))) urls.add(m[0])
  return Array.from(urls)
}

async function probe(p) {
  try {
    const r = await fetch(p.url, { headers: UA, signal: AbortSignal.timeout(15000) })
    const html = await r.text()
    const urls = extractUrls(html)
    console.log(`[${p.name}] HTTP ${r.status}, urls: ${urls.length}`)
    return urls
  } catch (e) { console.log(`[${p.name}] ERR ${e.message}`); return [] }
}

async function dl(url, name) {
  try {
    const r = await fetch(url, { headers: { 'user-agent': 'Mozilla/5.0' }, signal: AbortSignal.timeout(20000) })
    const ct = r.headers.get('content-type') || ''
    if (!r.ok || !ct.includes('image')) { console.log(`  [x] ${name}: ${r.status} ${ct}`); return }
    const b = Buffer.from(await r.arrayBuffer())
    const ext = ct.includes('png') ? 'png' : ct.includes('webp') ? 'webp' : 'jpg'
    fs.writeFileSync(path.join(OUT_DIR, `${name}.${ext}`), b)
    console.log(`  [+] ${name}: ${b.length} -> ${ext}`)
  } catch (e) { console.log(`  [x] ${name}: ${e.message}`) }
}

async function main() {
  for (const p of searches) {
    const urls = await probe(p)
    // 挑较大的（含大尺寸参数），下载前 6 个
    for (let i = 0; i < Math.min(urls.length, 6); i++) {
      await dl(urls[i], `${p.name}_${i}`)
    }
  }
}
main()
