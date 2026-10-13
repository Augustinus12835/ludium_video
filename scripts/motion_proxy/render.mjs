// node render.mjs <baseUrl> <scene> <outdir> [fps] [t1,t2,...]   — deterministic frame capture
import puppeteer from 'puppeteer-core';
import fs from 'fs';
const [base, scene, outdir, fpsArg, onlyT] = process.argv.slice(2);
const fps = Number(fpsArg || 24), DUR = 10.0;
fs.mkdirSync(outdir, {recursive: true});
const browser = await puppeteer.launch({executablePath: process.env.CHROME || '/usr/bin/google-chrome', headless: 'new',
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox']});
const page = await browser.newPage();
await page.setViewport({width: 1280, height: 720});
page.on('console', m => { if (!m.text().includes('404')) console.log('page:', m.text()); });
page.on('pageerror', e => console.log('pageerror:', e.message));
await page.goto(`${base}/page.html?scene=${scene}`);
await page.waitForFunction('window.ready === true', {timeout: 60000});
const ts = onlyT ? onlyT.split(',').map(Number) : [...Array(Math.round(DUR * fps)).keys()].map(i => i / fps);
for (const [i, t] of ts.entries()) {
  await page.evaluate(t => window.renderFrame(t), t);
  const el = await page.$('canvas');
  await el.screenshot({path: `${outdir}/f_${String(i).padStart(4, '0')}.png`});
}
await browser.close();
console.log('rendered', ts.length);
