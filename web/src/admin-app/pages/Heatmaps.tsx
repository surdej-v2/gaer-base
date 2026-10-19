import { useEffect, useRef, useState } from "react";
import { api, query } from "../../api";
import { Alert, fmtNumber, PageHeader, useLoad } from "../../ui";

type PageRow = { path: string; clicks: number; scrolls: number; pageviews: number };
type Heat = { path: string; clicks: [number, number][]; scroll: { depth: number; share: number }[]; scroll_samples: number };

// Draws click density: additive radial blobs on an alpha canvas, then colored blue → yellow → red.
function drawHeat(canvas: HTMLCanvasElement, points: [number, number][], width: number, height: number) {
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d")!;
  ctx.clearRect(0, 0, width, height);
  if (!points.length) return;
  const radius = 22;
  const alpha = Math.min(0.6, Math.max(0.06, 12 / points.length));
  for (const [x, y] of points) {
    const px = x * width, py = y * height;
    const g = ctx.createRadialGradient(px, py, 0, px, py, radius);
    g.addColorStop(0, `rgba(0,0,0,${alpha})`);
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g;
    ctx.fillRect(px - radius, py - radius, radius * 2, radius * 2);
  }
  const image = ctx.getImageData(0, 0, width, height);
  const d = image.data;
  for (let i = 0; i < d.length; i += 4) {
    const a = d[i + 3] / 255;
    if (a === 0) continue;
    const t = Math.min(1, a * 1.6);
    const [r, g, b] = t < 0.5 ? [37 + (250 - 37) * t * 2, 99 + (204 - 99) * t * 2, 235 - 214 * t * 2] : [250 - 11 * (t - 0.5) * 2, 204 - 136 * (t - 0.5) * 2, 21 + 47 * (t - 0.5) * 2];
    d[i] = r; d[i + 1] = g; d[i + 2] = b; d[i + 3] = Math.round(Math.min(0.85, 0.25 + t) * 255);
  }
  ctx.putImageData(image, 0, 0);
}

export default function Heatmaps() {
  const [days, setDays] = useState(30);
  const [device, setDevice] = useState("");
  const [path, setPath] = useState<string | null>(null);
  const pages = useLoad(() => api.get<PageRow[]>(`/api/admin/heatmaps/pages?days=${days}`), [days]);
  const heat = useLoad(() => (path ? api.get<Heat>(`/api/admin/heatmaps${query({ path, days, device })}`) : Promise.resolve(null)), [path, days, device]);
  const frame = useRef<HTMLIFrameElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);

  useEffect(() => {
    if (!path && pages.data?.length) setPath(pages.data[0].path);
  }, [pages.data, path]);

  const measure = () => {
    const doc = frame.current?.contentDocument?.documentElement;
    const w = wrap.current?.clientWidth ?? 1000;
    // Let the page render at full height so click positions line up with the content.
    setSize({ w, h: Math.max(doc?.scrollHeight ?? 800, 400) });
  };

  useEffect(() => {
    if (canvas.current && size && heat.data) drawHeat(canvas.current, heat.data.clicks, size.w, size.h);
  }, [size, heat.data]);

  return (
    <>
      <PageHeader eyebrow="/admin/heatmaps" title="Heatmaps">
        Klik og scroll opsamles af Python-serveren og gemmes i containerens egen database. Kun besøgende, der har
        sagt ja til statistik, indgår.
      </PageHeader>
      <div className="filters">
        <label className="field">Side
          <select value={path ?? ""} onChange={(e) => { setPath(e.target.value); setSize(null); }}>
            {pages.data?.length === 0 && <option value="">Ingen data endnu</option>}
            {pages.data?.map((p) => <option key={p.path} value={p.path}>{p.path} — {fmtNumber(p.clicks)} klik</option>)}
          </select>
        </label>
        <label className="field">Enhed
          <select value={device} onChange={(e) => setDevice(e.target.value)}>
            <option value="">Alle</option><option value="desktop">Computer</option><option value="mobile">Mobil</option><option value="tablet">Tablet</option>
          </select>
        </label>
        <label className="field">Periode
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>7 dage</option><option value={30}>30 dage</option><option value={90}>90 dage</option>
          </select>
        </label>
      </div>
      <Alert>{pages.error || heat.error}</Alert>
      {path && heat.data && (
        <div className="grid" style={{ "--min": "260px", gridTemplateColumns: "minmax(0, 3fr) minmax(220px, 1fr)" } as React.CSSProperties}>
          <div className="stack">
            <div className="spread small"><strong className="mono">{path}</strong><span className="muted">{fmtNumber(heat.data.clicks.length)} klik</span></div>
            <div className="heat-frame" ref={wrap}>
              <iframe ref={frame} src={path} title="Forhåndsvisning" onLoad={() => setTimeout(measure, 400)}
                      style={{ width: "100%", height: size?.h ?? 800 }} />
              <canvas ref={canvas} style={{ width: size?.w, height: size?.h }} />
            </div>
          </div>
          <div className="card">
            <span className="eyebrow">Scroll-dybde</span>
            <p className="muted tiny">Andel af visninger, der nåede mindst så langt ned · {fmtNumber(heat.data.scroll_samples)} målinger</p>
            {heat.data.scroll.map((s) => (
              <div key={s.depth} className="stack" style={{ "--gap": "3px" } as React.CSSProperties}>
                <div className="spread tiny"><span>{s.depth} %</span><span className="num">{Math.round(s.share * 100)} %</span></div>
                <div className="bar"><div style={{ width: `${s.share * 100}%`, background: "linear-gradient(to right, #f59e0b, #facc15)" }} /></div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
