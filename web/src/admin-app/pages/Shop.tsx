// /admin/shop — self-service shop: products (each with a QR code), printing labels, orders.
// Buyers scan the QR code, land on /w/butik/<code> and pay with MobilePay; no login needed.
import { useState } from "react";
import { api, errorText } from "../../api";
import { Alert, fmtDate, fmtNumber, Loading, PageHeader, Pager, useLoad } from "../../ui";

type Product = {
  id: number; code: string; url: string; name: string; description: string; price_ore: number;
  stock: number | null; active: boolean; sold: number; revenue_ore: number; updated_at: string;
};
type Order = {
  id: number; product_name: string; quantity: number; amount_ore: number; provider: string;
  status: "pending" | "paid" | "cancelled"; provider_state: string | null; created_at: string;
  paid_at: string | null; fulfilled_at: string | null;
};
type Orders = { items: Order[]; total: number; summary: { today_ore: number; today_orders: number; month_ore: number; unfulfilled: number } };
type Draft = { id?: number; name: string; description: string; price: string; stock: string; active: boolean };

const EMPTY: Draft = { name: "", description: "", price: "", stock: "", active: true };
const LIMIT = 50;

export const kr = (ore: number) =>
  (ore / 100).toLocaleString("da-DK", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " kr.";

/** "25", "25,5", "1.250,00" → øre. null when it is not a positive amount. */
function parseKr(value: string): number | null {
  const clean = value.replace(/\s|kr\.?/gi, "").replace(/\./g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) return null;
  const ore = Math.round(Number(clean) * 100);
  return ore > 0 ? ore : null;
}

const STATUS: Record<Order["status"], [string, string]> = {
  pending: ["Afventer", "amber"], paid: ["Betalt", "green"], cancelled: ["Afbrudt", "red"],
};

export default function Shop() {
  const [tab, setTab] = useState<"products" | "orders">("products");
  return (
    <>
      <div className="spread">
        <PageHeader eyebrow="/admin/shop" title="Butik">
          Selvbetjening: hver vare har en QR-kode. Kunden scanner koden, vælger antal og betaler med MobilePay.
          Kvitteringen viser ordrenummeret. Varerne ses også på <a href="/w/butik" target="_blank" rel="noreferrer">/w/butik</a>.
        </PageHeader>
        <div className="row">
          <button className={`btn ${tab === "products" ? "btn-dark" : "btn-outline"}`} onClick={() => setTab("products")}>Varer</button>
          <button className={`btn ${tab === "orders" ? "btn-dark" : "btn-outline"}`} onClick={() => setTab("orders")}>Ordrer</button>
        </div>
      </div>
      {tab === "products" ? <Products /> : <OrderList />}
    </>
  );
}

function Products() {
  const products = useLoad(() => api.get<Product[]>("/api/admin/shop/products"));
  const [draft, setDraft] = useState<Draft | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const edit = (p: Product) => {
    setNotice(null);
    setError(null);
    setDraft({ id: p.id, name: p.name, description: p.description, price: (p.price_ore / 100).toFixed(2).replace(".", ","),
               stock: p.stock === null ? "" : String(p.stock), active: p.active });
  };

  const save = async () => {
    if (!draft) return;
    setError(null);
    const price_ore = parseKr(draft.price);
    if (price_ore === null) return setError("Skriv en pris i kroner, fx 25 eller 12,50");
    if (draft.stock !== "" && !/^\d+$/.test(draft.stock)) return setError("Lager skal være et helt tal eller tomt");
    const body = { name: draft.name, description: draft.description, price_ore,
                   stock: draft.stock === "" ? null : Number(draft.stock), active: draft.active };
    try {
      if (draft.id) await api.put(`/api/admin/shop/products/${draft.id}`, body);
      else await api.post("/api/admin/shop/products", body);
      setNotice(`${draft.name} er gemt`);
      setDraft(null);
      await products.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const remove = async () => {
    if (!draft?.id) return;
    if (!confirm(`Slet ${draft.name}? QR-koden holder op med at virke. Gamle ordrer beholdes. Vil du hellere skjule varen, så fjern fluebenet ved "Til salg".`)) return;
    try {
      await api.del(`/api/admin/shop/products/${draft.id}`);
      setDraft(null);
      await products.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <>
      <div className="row">
        <button className="btn btn-primary" onClick={() => { setDraft({ ...EMPTY }); setNotice(null); setError(null); }}>Ny vare</button>
        <a className="btn btn-outline" href="/admin/shop/print" target="_blank" rel="noreferrer" style={{ color: "inherit", textDecoration: "none" }}>Udskriv alle QR-koder</a>
      </div>
      <Alert kind="success">{notice}</Alert>
      <Alert>{error || products.error}</Alert>
      {draft && (
        <div className="card" style={{ maxWidth: 720 }}>
          <span className="eyebrow">{draft.id ? "Redigér vare" : "Ny vare"}</span>
          <label className="field">Navn<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} autoFocus /></label>
          <label className="field">Beskrivelse (valgfri)<textarea value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
          <div className="filters">
            <label className="field">Pris (kr.)<input inputMode="decimal" value={draft.price} placeholder="25,00" onChange={(e) => setDraft({ ...draft, price: e.target.value })} /></label>
            <label className="field">Lager<input inputMode="numeric" value={draft.stock} placeholder="ubegrænset" onChange={(e) => setDraft({ ...draft, stock: e.target.value.trim() })} /></label>
          </div>
          <label className="row small"><input type="checkbox" checked={draft.active} onChange={(e) => setDraft({ ...draft, active: e.target.checked })} /> Til salg</label>
          <div className="row">
            <button className="btn btn-primary" onClick={save} disabled={!draft.name.trim()}>Gem</button>
            <button className="btn btn-outline" onClick={() => setDraft(null)}>Annullér</button>
            {draft.id && <button className="btn btn-danger" onClick={remove}>Slet</button>}
          </div>
        </div>
      )}
      {!products.data ? <Loading /> : products.data.length === 0 ? (
        <p className="muted">Ingen varer endnu. Opret den første, og udskriv dens QR-kode.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Vare</th><th>Pris</th><th>Lager</th><th>Solgt</th><th>Omsætning</th><th>Status</th><th>QR</th></tr></thead>
            <tbody>
              {products.data.map((p) => (
                <tr key={p.id} className="clickable" onClick={() => edit(p)}>
                  <td><strong>{p.name}</strong><div className="mono small muted">/w/butik/{p.code}</div></td>
                  <td className="num">{kr(p.price_ore)}</td>
                  <td className="num">{p.stock === null ? "∞" : fmtNumber(p.stock)}</td>
                  <td className="num">{fmtNumber(p.sold)}</td>
                  <td className="num">{kr(p.revenue_ore)}</td>
                  <td>
                    {!p.active ? <span className="pill">Skjult</span>
                      : p.stock === 0 ? <span className="pill red">Udsolgt</span>
                      : <span className="pill green">Til salg</span>}
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <div className="row" style={{ "--gap": "6px", flexWrap: "nowrap" } as React.CSSProperties}>
                      <a className="btn btn-outline btn-sm" href={`/admin/shop/print?ids=${p.id}`} target="_blank" rel="noreferrer" style={{ color: "inherit", textDecoration: "none" }}>Udskriv</a>
                      <a className="btn btn-ghost btn-sm" href={p.url} target="_blank" rel="noreferrer">Åbn</a>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function OrderList() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const orders = useLoad(
    () => api.get<Orders>(`/api/admin/shop/orders?limit=${LIMIT}&offset=${offset}${status ? `&status=${status}` : ""}`),
    [status, offset],
  );

  const act = async (path: string) => {
    setError(null);
    try {
      await api.post(path);
      await orders.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const s = orders.data?.summary;
  return (
    <>
      {s && (
        <div className="grid" style={{ "--min": "200px" } as React.CSSProperties}>
          <div className="card"><span className="eyebrow">I dag</span><span className="stat">{kr(s.today_ore)}</span><span className="muted small">{fmtNumber(s.today_orders)} ordrer</span></div>
          <div className="card"><span className="eyebrow">Seneste 30 dage</span><span className="stat">{kr(s.month_ore)}</span></div>
          <div className="card"><span className="eyebrow">Mangler udlevering</span><span className="stat">{fmtNumber(s.unfulfilled)}</span></div>
        </div>
      )}
      <div className="row">
        <label className="field">Vis
          <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
            <option value="">Alle</option>
            <option value="unfulfilled">Betalt, ikke udleveret</option>
            <option value="paid">Betalt</option>
            <option value="pending">Afventer betaling</option>
            <option value="cancelled">Afbrudt</option>
          </select>
        </label>
        <button className="btn btn-outline" style={{ alignSelf: "end" }} onClick={() => orders.reload()}>Opdatér</button>
      </div>
      <Alert>{error || orders.error}</Alert>
      {!orders.data ? <Loading /> : (
        <>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Ordre</th><th>Tid</th><th>Vare</th><th>Antal</th><th>Beløb</th><th>Status</th><th>Udlevering</th></tr></thead>
              <tbody>
                {orders.data.items.map((o) => (
                  <tr key={o.id}>
                    <td className="num"><strong>#{o.id}</strong>{o.provider === "console" && <div className="small muted">test</div>}</td>
                    <td>{fmtDate(o.paid_at ?? o.created_at)}</td>
                    <td>{o.product_name}</td>
                    <td className="num">{o.quantity}</td>
                    <td className="num">{kr(o.amount_ore)}</td>
                    <td><span className={`pill ${STATUS[o.status][1]}`}>{STATUS[o.status][0]}</span></td>
                    <td>
                      {o.status === "paid" && (o.fulfilled_at
                        ? <span className="small muted">{fmtDate(o.fulfilled_at)}</span>
                        : <button className="btn btn-outline btn-sm" onClick={() => act(`/api/admin/shop/orders/${o.id}/fulfil`)}>Udlevér</button>)}
                      {o.status === "pending" && (
                        <button className="btn btn-ghost btn-sm" onClick={() => act(`/api/admin/shop/orders/${o.id}/sync`)}>Tjek betaling</button>
                      )}
                    </td>
                  </tr>
                ))}
                {orders.data.items.length === 0 && <tr><td colSpan={7} className="muted">Ingen ordrer.</td></tr>}
              </tbody>
            </table>
          </div>
          <Pager total={orders.data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
        </>
      )}
    </>
  );
}
