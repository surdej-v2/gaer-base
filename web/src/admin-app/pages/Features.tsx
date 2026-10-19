// /admin/features → Features og ringe (screen 05), after happy-groups FeaturesAdmin.tsx.
import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Download, Plus, Trash2 } from "lucide-react";
import { api, errorText } from "../../api";
import { useLoad } from "../../ui";
import { useAdmin } from "../context";
import { AREA, card, fmt, input, label, lastLabel, MONO, phoneLabel, RING, SANS, select, tableHead, TONE } from "../theme";
import { Btn, ErrorBox, IconBtn, Modal, PageTitle, Toggle, useToast } from "../ui";
import type { UsersResponse } from "../users";

type Feature = { key: string; title: string; description: string; area: string; ring: number; observe: boolean; builtin: boolean; uses_30d: number; users_30d: number };
type Level = { user_id: number; ring: number; display_name: string | null; phone: string };
type FeaturesResponse = { items: Feature[]; missing: { key: string }[]; levels: Level[] };
type Usage = { user_id: number | null; display_name: string | null; phone: string | null; n: number; last: string };

const COLS = "minmax(200px,1.2fr) 100px minmax(200px,1.4fr) 230px 90px 150px 36px";
const KEY_RE = /^[a-z0-9][a-z0-9-]*$/;
const small = { height: 30, padding: "0 6px", border: "1px solid #e2e8f0", borderRadius: 8, font: `12px ${SANS}`, background: "#ffffff" } as const;

function RingOptions() {
  return <>{[1, 2, 3, 4].map((n) => <option key={n} value={n}>Ring {n} · {RING[n][0]}</option>)}</>;
}

function RingChip({ ring }: { ring: number }) {
  const [bg, fg] = TONE[RING[ring][1]];
  return <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 7px", borderRadius: 9999, background: bg, color: fg }}>R{ring}</span>;
}

export default function Features() {
  const say = useToast();
  const { setMissingFeatures } = useAdmin();
  const { data, error, reload, setData } = useLoad(() => api.get<FeaturesResponse>("/api/admin/features"), []);
  const users = useLoad(() => api.get<UsersResponse>("/api/admin/users?limit=500"), []);
  const [open, setOpen] = useState<string | null>(null);
  const [usage, setUsage] = useState<Usage[] | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [nf, setNf] = useState({ key: "", title: "", description: "", area: "bruger" });
  const [lvlUser, setLvlUser] = useState("");
  const [lvlRing, setLvlRing] = useState("2");

  useEffect(() => {
    if (data) setMissingFeatures(data.missing.length);
  }, [data, setMissingFeatures]);

  useEffect(() => {
    setUsage(null);
    if (open) api.get<Usage[]>(`/api/admin/features/${open}/usage`).then(setUsage, () => setUsage([]));
  }, [open]);

  const act = async (call: () => Promise<unknown>, ok?: string) => {
    try {
      await call();
      if (ok) say(ok);
      await reload();
    } catch (e) {
      say(errorText(e));
    }
  };

  const patch = (f: Feature, body: { ring?: number; observe?: boolean }) => {
    // Optimistic, so the select and the toggle answer at once.
    setData((d) => d && { ...d, items: d.items.map((x) => (x.key === f.key ? { ...x, ...body } : x)) });
    return act(() => api.patch(`/api/admin/features/${f.key}`, body), body.ring ? `${f.title} er nu i ring ${body.ring} · ${RING[body.ring][0]}` : undefined);
  };

  const items = data?.items ?? [];
  const missing = data?.missing.length ?? 0;
  const levels = data?.levels ?? [];
  const realUsers = (users.data?.items ?? []).filter((u) => !u.impersonation_only && !levels.some((l) => l.user_id === u.id));
  const nfOk = KEY_RE.test(nf.key) && !!nf.title.trim() && !items.some((f) => f.key === nf.key);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PageTitle title="Features og ringe" aside={<>
        <Btn icon={Download} height={34} style={{ padding: "0 12px" }} disabled={!missing}
          onClick={() => act(async () => {
            const r = await api.post<{ created: string[] }>("/api/admin/features/import");
            say(`${r.created.length} features importeret i ring 1`);
          })}>Importér fra koden ({missing})</Btn>
        <Btn kind="primary" icon={Plus} height={34} style={{ padding: "0 12px" }} onClick={() => setNewOpen(true)}>Ny feature</Btn>
      </>}>
        Ringen sættes her og kun her. Nye features starter i ring 1. Alt er udrullet overalt, ringen afgør hvem der ser det.
      </PageTitle>
      {error && <ErrorBox>{error}</ErrorBox>}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 10 }}>
        {[1, 2, 3, 4].map((n) => {
          const [bg, fg] = TONE[RING[n][1]];
          return (
            <div key={n} style={{ display: "flex", flexDirection: "column", gap: 6, padding: "12px 14px", borderRadius: 12, background: "#ffffff", border: "1px solid #e2e8f0" }}>
              <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 9999, background: bg, color: fg }}>Ring {n} · {RING[n][0]}</span>
                <span style={{ marginLeft: "auto", fontSize: 12, color: "#64748b", fontVariantNumeric: "tabular-nums" }}>{items.filter((f) => f.ring === n).length} features</span>
              </span>
              <span style={{ fontSize: 12, color: "#64748b", textWrap: "pretty" }}>{RING[n][2]}</span>
            </div>
          );
        })}
      </div>

      <div style={{ ...card, overflow: "hidden" }}>
        <div style={{ overflowX: "auto" }}>
          <div style={{ minWidth: 960 }}>
            <div style={{ ...tableHead, gridTemplateColumns: COLS }}>
              <span>Feature</span><span>Område</span><span>Beskrivelse</span><span>Ring</span><span>Observation</span>
              <span style={{ textAlign: "right" }}>Brug 30 d</span><span />
            </div>
            {items.map((f) => {
              const isOpen = open === f.key && f.users_30d > 0;
              const Chev = open === f.key ? ChevronDown : ChevronRight;
              return (
                <div key={f.key} style={{ borderBottom: "1px solid #f1f5f9" }}>
                  <div style={{ display: "grid", gridTemplateColumns: COLS, gap: 10, alignItems: "center", padding: "10px 16px", fontSize: 13 }}>
                    <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
                      <span style={{ fontWeight: 600 }}>{f.title}</span>
                      <span style={{ font: `11px ${MONO}`, color: "#64748b" }}>{f.key}</span>
                    </span>
                    <span>{AREA[f.area] ?? f.area}</span>
                    <span title={f.description} style={{ color: "#64748b", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{f.description}</span>
                    <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <RingChip ring={f.ring} />
                      <select aria-label="Ring" value={String(f.ring)} onChange={(e) => patch(f, { ring: Number(e.target.value) })} style={{ ...small, width: 170 }}>
                        <RingOptions />
                      </select>
                    </span>
                    <Toggle on={f.observe} label="Observation" onChange={() => patch(f, { observe: !f.observe })} />
                    <button type="button" className="tg-hover" onClick={() => setOpen(open === f.key ? null : f.key)}
                      style={{ justifySelf: "end", display: "inline-flex", alignItems: "center", gap: 4, border: 0, background: "transparent", cursor: "pointer",
                        padding: "2px 4px", borderRadius: 6, font: `13px ${SANS}`, color: "#020817", fontVariantNumeric: "tabular-nums" }}>
                      {fmt(f.uses_30d)} <span style={{ fontSize: 12, color: "#64748b" }}>/ {f.users_30d} brugere</span>
                      <Chev size={12} color="#64748b" strokeWidth={2} />
                    </button>
                    <span>
                      {!f.builtin && <IconBtn icon={Trash2} label="Slet" size={28} iconSize={15}
                        onClick={() => act(() => api.del(`/api/admin/features/${f.key}`), `${f.title} er slettet`)} />}
                    </span>
                  </div>
                  {isOpen && (
                    <div style={{ padding: "10px 16px 14px", background: "#f8fafc", display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: "6px 24px", fontSize: 13 }}>
                      {usage === null && <span style={{ color: "#64748b" }}>Henter …</span>}
                      {usage?.map((x) => (
                        <div key={x.user_id ?? "?"} style={{ display: "flex", gap: 8 }}>
                          <span>{x.display_name || phoneLabel(x.phone) || `#${x.user_id}`}</span>
                          <span style={{ marginLeft: "auto", color: "#64748b", fontVariantNumeric: "tabular-nums" }}>{fmt(x.n)} · {lastLabel(x.last)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div style={{ ...card, padding: 20, display: "flex", flexDirection: "column", gap: 10, maxWidth: 720 }}>
        <span style={{ fontSize: 15, fontWeight: 600 }}>Brugernes niveau</span>
        <span style={{ fontSize: 12, color: "#64748b", textWrap: "pretty" }}>
          Uden række gælder ring 4. Brugeren vælger selv under Konto → Dit niveau, og du kan sætte eller nulstille det her. En feature vises, når
          dens ring er mindst brugerens niveau.
        </span>
        {levels.map((l) => (
          <div key={l.user_id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0", borderTop: "1px solid #f1f5f9", fontSize: 13 }}>
            <span style={{ flex: 1, minWidth: 0 }}>
              {l.display_name || phoneLabel(l.phone)}{" "}
              <span style={{ color: "#64748b", fontVariantNumeric: "tabular-nums" }}>{l.display_name ? phoneLabel(l.phone) : ""}</span>
            </span>
            <select value={String(l.ring)} style={small}
              onChange={(e) => act(() => api.put(`/api/admin/features/levels/${l.user_id}`, { ring: Number(e.target.value) }))}>
              <RingOptions />
            </select>
            <button type="button" className="tg-hover" onClick={() => act(() => api.del(`/api/admin/features/levels/${l.user_id}`))}
              style={{ height: 30, padding: "0 10px", border: 0, borderRadius: 8, background: "transparent", cursor: "pointer", font: `500 12px ${SANS}`, color: "#020817" }}>
              Nulstil
            </button>
          </div>
        ))}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", paddingTop: 8, borderTop: "1px solid #f1f5f9" }}>
          <select value={lvlUser} onChange={(e) => setLvlUser(e.target.value)} style={{ ...small, height: 32, minWidth: 220 }}>
            <option value="">Vælg bruger</option>
            {realUsers.map((u) => (
              <option key={u.id} value={u.id}>{u.display_name ? `${u.display_name} · ${phoneLabel(u.phone)}` : phoneLabel(u.phone)}</option>
            ))}
          </select>
          <select value={lvlRing} onChange={(e) => setLvlRing(e.target.value)} style={{ ...small, height: 32 }}><RingOptions /></select>
          <Btn height={32} disabled={!lvlUser} style={{ padding: "0 12px", font: `500 12px ${SANS}` }}
            onClick={() => act(async () => {
              await api.put(`/api/admin/features/levels/${lvlUser}`, { ring: Number(lvlRing) });
              setLvlUser("");
            })}>Sæt</Btn>
        </div>
      </div>

      {newOpen && (
        <Modal onClose={() => setNewOpen(false)}>
          <span style={{ fontSize: 18, fontWeight: 700 }}>Ny feature</span>
          <label style={label}>Nøgle
            <input value={nf.key} onChange={(e) => setNf({ ...nf, key: e.target.value.toLowerCase() })} placeholder="min-feature" style={{ ...input, font: `14px ${MONO}` }} />
          </label>
          <label style={label}>Titel<input value={nf.title} onChange={(e) => setNf({ ...nf, title: e.target.value })} style={input} /></label>
          <label style={label}>Beskrivelse<input value={nf.description} onChange={(e) => setNf({ ...nf, description: e.target.value })} style={input} /></label>
          <label style={label}>Område
            <select value={nf.area} onChange={(e) => setNf({ ...nf, area: e.target.value })} style={select}>
              {Object.entries(AREA).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <span style={{ fontSize: 12, color: "#64748b" }}>Starter i ring 1 (Internt). Nøglen må kun have små bogstaver, tal og bindestreg.</span>
          <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <Btn onClick={() => setNewOpen(false)}>Annullér</Btn>
            <Btn kind="primary" disabled={!nfOk} style={{ padding: "0 16px" }} onClick={() => act(async () => {
              await api.post("/api/admin/features", nf);
              setNewOpen(false);
              setNf({ key: "", title: "", description: "", area: "bruger" });
            }, `${nf.title.trim()} er oprettet i ring 1`)}>Opret</Btn>
          </div>
        </Modal>
      )}
    </div>
  );
}
