// Renders the brand assets from web/public/favicon.svg (the Happy Mates mark): the PWA, Office and extension icons
// and the generated og-images in server/app/web_static/og/ (app/og.py). Admins replace the og-images in /admin/web.
// Run from the repo root with Playwright available: node web/scripts/brand-assets.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const R = require('path').resolve(__dirname, '..', '..');
const mark = fs.readFileSync(R + '/web/public/favicon.svg', 'utf8');
const icon = (size, pad, bg) => `<html><body style="margin:0;width:${size}px;height:${size}px;background:${bg};display:flex;align-items:center;justify-content:center">
  <div style="width:${size - 2 * pad}px;height:${size - 2 * pad}px">${mark.replace('<svg ', '<svg width="100%" height="100%" ')}</div></body></html>`;
const ICONS = [
  ['web/public/m/icon-192.png', 192, 26, '#ffffff'], ['web/public/m/icon-512.png', 512, 70, '#ffffff'],
  ['web/public/office/icon-16.png', 16, 0, 'transparent'], ['web/public/office/icon-32.png', 32, 1, 'transparent'],
  ['web/public/office/icon-64.png', 64, 2, 'transparent'], ['web/public/office/icon-80.png', 80, 3, 'transparent'],
  ['extension/icons/icon-16.png', 16, 0, 'transparent'], ['extension/icons/icon-32.png', 32, 1, 'transparent'],
  ['extension/icons/icon-48.png', 48, 2, 'transparent'], ['extension/icons/icon-128.png', 128, 6, 'transparent'],
];
// og-images: 1200×630, the brand rainbow as a bar, the mark, the name and one line for the surface.
const OG = {
  default: ['Tørgær', 'Klip, filer og en assistent — samlet ét sted'],
  w: ['Velkommen til Tørgær', 'Klip, filer og en assistent — samlet ét sted'],
  login: ['Log ind på Tørgær', 'Engangskode på mail · passkey'],
  privatliv: ['Privatliv', 'Sådan passer Tørgær på dine data'],
};
const og = (title, line) => `<html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@500;700;800&display=swap"></head>
<body style="margin:0;width:1200px;height:630px;font-family:Inter,system-ui,sans-serif;background:#fff;color:#020817;position:relative;overflow:hidden">
  <div style="position:absolute;inset:0 0 auto 0;height:14px;background:linear-gradient(90deg,#E63946 0%,#F77F00 25%,#FCBF49 50%,#06D6A0 75%,#118AB2 100%)"></div>
  <div style="position:absolute;right:-120px;bottom:-160px;width:620px;height:620px;opacity:.10">${mark.replace('<svg ', '<svg width="100%" height="100%" ')}</div>
  <div style="position:absolute;left:96px;top:96px;display:flex;align-items:center;gap:22px">
    <div style="width:88px;height:88px">${mark.replace('<svg ', '<svg width="100%" height="100%" ')}</div>
    <div style="display:flex;flex-direction:column;gap:6px">
      <span style="font-size:34px;font-weight:800;letter-spacing:.06em;background:linear-gradient(90deg,#E63946,#F77F00 30%,#06D6A0 70%,#118AB2);-webkit-background-clip:text;background-clip:text;color:transparent">TØRGÆR</span>
      <span style="font-size:20px;font-weight:500;color:#475569">by Happy Mates</span>
    </div>
  </div>
  <div style="position:absolute;left:96px;right:300px;bottom:110px;display:flex;flex-direction:column;gap:20px">
    <div style="font-size:76px;font-weight:800;letter-spacing:-.03em;line-height:1.02">${title}</div>
    <div style="font-size:32px;font-weight:500;color:#334155">${line}</div>
  </div>
</body></html>`;
(async () => {
  const b = await chromium.launch();
  for (const [path, size, pad, bg] of ICONS) {
    const p = await b.newPage({ viewport: { width: size, height: size } });
    await p.setContent(icon(size, pad, bg));
    await p.screenshot({ path: R + '/' + path, omitBackground: bg === 'transparent' });
    await p.close();
  }
  for (const [name, [title, line]] of Object.entries(OG)) {
    const p = await b.newPage({ viewport: { width: 1200, height: 630 } });
    await p.setContent(og(title, line), { waitUntil: 'networkidle' });
    await p.evaluate(() => document.fonts.ready);
    await p.screenshot({ path: `${R}/server/app/web_static/og/${name}.png` });
    await p.close();
  }
  await b.close();
})();
