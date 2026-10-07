// Shoots the mockups of the built prototype (dist/) into ../images:
//   npm run build && node scripts/shoot.mjs [name ...]
// PNG screens at 2x, GIF animations from frozen moments of a morph.
import { createServer } from 'node:http'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { extname, join, resolve } from 'node:path'
import { chromium } from 'playwright-core'
import { PNG } from 'pngjs'
import gifenc from 'gifenc'

const { GIFEncoder, quantize, applyPalette } = gifenc
const ROOT = resolve(import.meta.dirname, '..')
const DIST = join(ROOT, 'dist')
const OUT = resolve(process.env.OUT ?? join(ROOT, '..', 'images'))
const CHROME = process.env.CHROME ?? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'

const W = 1200
/** name, route, viewport width and height; `sel` shoots one element instead of the page. */
export const SHOTS = [
  { name: '01-home', route: '/home', h: 640 },
  { name: '02-editor-new', route: '/prompt?new', h: 900 },
  { name: '02-editor-new-ows', route: '/prompt?new&view=ows', h: 780 },
  { name: '03-editor-graph', route: '/studio', h: 900 },
  { name: '03-editor-ows', route: '/ows', h: 780 },
  { name: '04-editor-prompt', route: '/prompt', h: 900 },
  { name: '05-changes-graph', route: '/changes', h: 960 },
  { name: '05-changes-prompt', route: '/changes?view=prompt', h: 640 },
  { name: '05-changes-ows', route: '/changes?view=ows', h: 700 },
  { name: '06-diff-overlay', route: '/diff', h: 1060 },
  { name: '06-diff-side', route: '/diff?mode=side', h: 1040 },
  { name: '07-publish', route: '/versions', h: 1100 },
  { name: '07-publish-experiment', route: '/versions?experiment', h: 1110 },
  { name: '08-rollout', route: '/rollout', h: 780 },
  { name: '09-scenarios', route: '/scenarios', h: 560 },
  { name: '10-runs', route: '/runs', h: 700 },
  { name: '11-run', route: '/run', h: 820 },
  { name: '12-migration', route: '/campaign', h: 900 },
  { name: '13-inbox', route: '/inbox', h: 800 },
  { name: 'd1-edit', route: '/d/model', w: 900, h: 740, sel: '[data-shot=diagram]' },
  { name: 'd2-clarify', route: '/d/clarify', w: 900, h: 480, sel: '[data-shot=diagram]' },
  { name: 'd3-lifecycle', route: '/d/lifecycle', w: 900, h: 460, sel: '[data-shot=diagram]' },
  { name: 'd4-map', route: '/d/map', w: 900, h: 860, sel: '[data-shot=diagram]' },
]

/** name, route, moments: [{from, to, t, hold}] — hold repeats a frame. */
export const ANIMATIONS = [
  { name: '06-diff-morph', route: '/diff', h: 1060, sel: '[data-shot=graph]', scale: 0.8, moments: [...sweep('before', 'after', 18, 14), ...sweep('after', 'before', 18, 14).slice(1)] },
  { name: '02-editor-new-morph', route: '/prompt?new', h: 900, sel: '[data-shot=graph]', scale: 0.9, moments: [...sweep('A', 'B', 14, 12), ...sweep('B', 'C', 14, 12).slice(1), ...sweep('C', 'A', 14, 12).slice(1)] },
]

function sweep(from, to, n, hold) {
  const out = [{ to: from, t: 1, hold }]
  for (let i = 1; i <= n; i++) out.push({ from, to, t: i / n })
  out.push({ to, t: 1, hold })
  return out
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.svg': 'image/svg+xml' }

function serve() {
  const server = createServer(async (req, res) => {
    const path = decodeURIComponent(new URL(req.url, 'http://x').pathname)
    const file = join(DIST, path === '/' ? 'index.html' : path)
    let body
    try {
      body = await readFile(file)
    } catch {
      res.writeHead(404).end()
      return
    }
    res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' })
    res.end(body)
  })
  return new Promise((ok) => server.listen(0, '127.0.0.1', () => ok(server)))
}

async function open(browser, base, route, h, scale = 2, w = W) {
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: scale })
  await page.goto(`${base}/?shot=1#${route}`)
  await page.waitForFunction(() => window.__ready === true, null, { timeout: 20000 })
  await page.waitForTimeout(400)
  return page
}

async function gif(page, anim) {
  const enc = GIFEncoder()
  let palette
  const clip = anim.sel ? await page.locator(anim.sel).boundingBox() : anim.clip
  for (const m of anim.moments) {
    await page.evaluate((f) => window.__freeze(f), m)
    await page.waitForTimeout(60)
    const buf = await page.screenshot({ clip })
    const png = PNG.sync.read(buf)
    palette ??= quantize(png.data, 256)
    const index = applyPalette(png.data, palette)
    enc.writeFrame(index, png.width, png.height, { palette, delay: 60 * (m.hold ?? 1) })
  }
  enc.finish()
  return Buffer.from(enc.bytes())
}

const only = new Set(process.argv.slice(2))
if (!existsSync(DIST)) throw new Error('build first: npm run build')
await mkdir(OUT, { recursive: true })
const server = await serve()
const base = `http://127.0.0.1:${server.address().port}`
const browser = await chromium.launch({ executablePath: CHROME })
try {
  for (const s of SHOTS) {
    if (only.size && !only.has(s.name)) continue
    const page = await open(browser, base, s.route, s.h, 2, s.w)
    if (s.sel) await page.locator(s.sel).screenshot({ path: join(OUT, `${s.name}.png`) })
    else await page.screenshot({ path: join(OUT, `${s.name}.png`), fullPage: true })
    await page.close()
    console.log('png', s.name)
  }
  for (const a of ANIMATIONS) {
    if (only.size && !only.has(a.name)) continue
    const page = await open(browser, base, a.route, a.h, a.scale ?? 1)
    await writeFile(join(OUT, `${a.name}.gif`), await gif(page, a))
    await page.close()
    console.log('gif', a.name)
  }
} finally {
  await browser.close()
  server.close()
}
