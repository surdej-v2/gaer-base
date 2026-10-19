// Draws store graphics as PNG in the browser (Hent PNG). Same composition as the previews on the
// Grafik tab: logo on the icon background, brand-coloured screenshots with a caption and a phone screen.
import { assetBg, onDark, type Asset, type Cfg } from "./model";

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
  ctx.fill();
}

function wrap(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    const next = line ? `${line} ${word}` : word;
    if (ctx.measureText(next).width > maxWidth && line) {
      lines.push(line);
      line = word;
    } else line = next;
  }
  if (line) lines.push(line);
  return lines;
}

export async function renderPng(a: Asset, cfg: Cfg, logoUrl: string, caption: string): Promise<Blob> {
  const canvas = document.createElement("canvas");
  canvas.width = a.w;
  canvas.height = a.h;
  const ctx = canvas.getContext("2d")!;
  const bg = assetBg(a, cfg);
  // Store icons must not be transparent; the adaptive foreground and the monochrome icon are.
  if (a.kind !== "adaptive" && a.kind !== "mono") {
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, a.w, a.h);
  }
  const fg = onDark(bg) ? "#ffffff" : "#0f172a";
  const font = getComputedStyle(document.body).fontFamily || "Inter, sans-serif";

  if (a.kind === "shot") {
    const pad = a.w * 0.06;
    ctx.fillStyle = fg;
    ctx.textAlign = "center";
    ctx.font = `700 ${Math.round(a.w * 0.07)}px ${font}`;
    const lines = wrap(ctx, caption, a.w - pad * 2);
    const lh = a.w * 0.085;
    lines.forEach((l, i) => ctx.fillText(l, a.w / 2, pad + lh * (i + 1)));
    const sw = a.w * (a.device === "tablet" ? 0.84 : 0.76), sx = (a.w - sw) / 2, sy = pad + lh * lines.length + pad;
    ctx.fillStyle = "#ffffff";
    ctx.shadowColor = "rgba(2,8,23,0.15)";
    ctx.shadowBlur = a.w * 0.02;
    roundRect(ctx, sx, sy, sw, a.h - sy + a.w * 0.05, a.w * 0.04);
    ctx.shadowBlur = 0;
    const ip = sw * 0.06, rowH = sw * 0.11;
    ctx.fillStyle = cfg.primary;
    ctx.globalAlpha = 0.85;
    roundRect(ctx, sx + ip, sy + ip, sw * 0.55, rowH * 0.45, rowH * 0.2);
    ctx.globalAlpha = 1;
    ctx.fillStyle = "#f1f5f9";
    for (let i = 0; i < 3; i++) roundRect(ctx, sx + ip, sy + ip + rowH * (0.9 + i * 1.25), (sw - ip * 2) * (i === 2 ? 0.7 : 1), rowH, rowH * 0.25);
  } else if (a.kind === "feature") {
    ctx.fillStyle = fg;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = `700 ${Math.round(a.h * 0.16)}px ${font}`;
    ctx.fillText(cfg.name, a.w / 2, a.h / 2);
  } else {
    const img = await loadImage(logoUrl);
    const size = a.w * (a.kind === "adaptive" ? 0.44 : 0.58); // the adaptive foreground stays inside the 66 % safe zone
    if (a.kind === "mono") ctx.filter = "grayscale(1) brightness(2.4)";
    ctx.drawImage(img, (a.w - size) / 2, (a.h - size) / 2, size, size);
    ctx.filter = "none";
  }
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("PNG kunne ikke tegnes"))), "image/png"));
}

export function saveBlob(blob: Blob, filename: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
