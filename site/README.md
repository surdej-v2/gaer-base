# GitHub Pages: Gær butik

Static presentation of the self-service shop plus a click-through demo of the buyer flow that runs
entirely in the browser (`demo.html`, state in localStorage, no real payment). The real shop lives in
`server/app/routes/shop.py` and `/admin/shop`.

- `index.html` — presentation, screenshots, QR codes for the demo products
- `demo.html` + `assets/demo.js` — same markup and CSS (`assets/w.css`) as `/w/butik`
- `etiketter.html` + `assets/qr.js` — printable labels; QR codes are drawn in the browser from the page's
  own address, so they work on any Pages URL (`assets/qrcode.js`: qrcode-generator 1.4.4, MIT)

Publish: `git subtree split --prefix site -b gh-pages-build && git push origin gh-pages-build:gh-pages --force`
