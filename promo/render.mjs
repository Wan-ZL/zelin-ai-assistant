// Frame-by-frame capture of the render stage. Deterministic: seeks the
// stage's timeline frame by frame and screenshots each one.
//
//   cd promo && npm install --no-save playwright-core && cd ..
//   node promo/render.mjs [--fps 30] [--vertical]
//
// Uses the Playwright chromium already cached under ~/Library/Caches, or
// system Chrome, or $PROMO_CHROME.
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

process.chdir(path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'));

const argv = process.argv.slice(2);
const opt = (n, d) => { const i = argv.indexOf(`--${n}`); return i >= 0 ? argv[i + 1] : d; };
const vertical = argv.includes('--vertical');
const FPS = parseInt(opt('fps', '30'), 10);
const W = vertical ? 1080 : 1920;
const H = vertical ? 1920 : 1080;
const dir = path.resolve(`promo/build/frames${vertical ? '-v' : ''}`);

function findChrome() {
  if (process.env.PROMO_CHROME) return process.env.PROMO_CHROME;
  const base = path.join(process.env.HOME, 'Library/Caches/ms-playwright');
  if (fs.existsSync(base)) {
    for (const d of fs.readdirSync(base).filter((x) => x.startsWith('chromium-')).sort().reverse()) {
      for (const app of [
        'chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing',
        'chrome-mac/Chromium.app/Contents/MacOS/Chromium',
      ]) {
        const p = path.join(base, d, app);
        if (fs.existsSync(p)) return p;
      }
    }
  }
  const sys = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  if (fs.existsSync(sys)) return sys;
  throw new Error('no chromium found — set PROMO_CHROME=/path/to/chrome');
}

fs.rmSync(dir, { recursive: true, force: true });
fs.mkdirSync(dir, { recursive: true });

const browser = await chromium.launch({ executablePath: findChrome(), headless: true });
const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
page.on('pageerror', (e) => { console.error('PAGE ERROR:', e.message); process.exitCode = 1; });
await page.goto('file://' + path.resolve('promo/stage/index.html'));
await page.waitForFunction('window.STAGE_READY === true', null, { timeout: 30000 });

const duration = await page.evaluate('TL.duration');
const total = Math.ceil(duration * FPS);
console.log(`rendering ${total} frames @ ${FPS} fps (${W}x${H})`);
for (let f = 0; f < total; f++) {
  await page.evaluate(`window.seek(${(f / FPS).toFixed(5)})`);
  await page.screenshot({ path: path.join(dir, `f${String(f).padStart(5, '0')}.png`) });
  if (f % 300 === 0) console.log(`  frame ${f}/${total}`);
}
await browser.close();
console.log(`frames -> ${dir}`);
