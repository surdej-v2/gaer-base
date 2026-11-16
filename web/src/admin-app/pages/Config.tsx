// /admin/config — Konfiguration (server/app/config_store.py, config_registry.py). Settings an admin may change instead
// of an environment variable: plain values in config_values, keys and tokens encrypted in config_secrets (written, never
// shown again). The order is database → environment → default. Saving needs a step-up (the api client opens the
// dialog) and is audited. What must never be set from a browser stays in the environment and is only listed here.
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { useSearchParams } from "react-router-dom";
import { CheckCircle2, Download, KeyRound, Lock, Mail, RotateCcw, Server, SlidersHorizontal } from "lucide-react";
import { api, errorText } from "../../api";
import { Alert, Btn, fmtDate, Loading, PageTitle, Pill, useLoad, useToast } from "../ui";
import { card, MONO, SANS, type Tone } from "../theme";

type Source = "db" | "env" | "default" | "none";
type Item = {
  key: string; env: string; label: string; help: string; kind: string; options: string[]; min: number | null; max: number | null;
  max_len: number; allow_empty: boolean; secret: boolean; endpoint: string | null; provider: string | null; restart: boolean;
  blocked: string[]; source: Source; updated_at?: string | null;
  value?: unknown; fallback?: unknown;                                  // plain values
  set?: boolean; ignored?: string | null; set_at?: string | null;       // secrets
};
type Group = { key: string; label: string; description: string; items: Item[] };
type EnvOnly = { env: string; category: "manual" | "compose" | "image" | "optional" | "web"; reason: string; set: boolean };
type Overview = { groups: Group[]; env_only: EnvOnly[]; importable: number; production: boolean };
type SaveResult = { changed: string[]; secrets_set: string[]; secrets_removed: string[]; secrets_cleared: string[]; restart: string[] };

const SOURCE: Record<Source, [string, Tone]> = {
  db: ["Gemt her", "blue"], env: ["Fra miljøet", "orange"], default: ["Standard", "gray"], none: ["Ikke sat", "gray"],
};
const CATEGORY: Record<EnvOnly["category"], [string, string]> = {
  manual: ["Skal sættes i hånden", "Rod-nøgler, domænet og lageret. Uden dem starter appen ikke, eller den kan ikke læse sine egne hemmeligheder."],
  compose: ["Sættes af compose eller Fleet", "Databaseroller, NATS og domænealiaser. Ingen skal skrive dem."],
  image: ["Standard i imaget", "Proces- og stiindstillinger, og kontakter der aldrig må kunne slås til fra en browser."],
  optional: ["Kun hvis funktionen bruges", "Azure i stedet for S3, Stalwart, Redis, analysedatabaser og YubiKey-rødder."],
  web: ["Vælges et andet sted i /admin", "Miljøet giver kun standardværdien."],
};
const PROVIDER_KEY: Record<string, string> = { mail: "mail_provider", sms: "sms_provider" };

const input: CSSProperties = { height: 38, padding: "0 12px", borderRadius: 8, border: "1px solid #cbd5e1", background: "#ffffff",
  font: `14px ${SANS}`, color: "#020817", width: "100%", boxSizing: "border-box" };
const label: CSSProperties = { fontSize: 13.5, fontWeight: 600 };
const hint: CSSProperties = { fontSize: 12.5, color: "#64748b", lineHeight: 1.45 };
const mono: CSSProperties = { fontFamily: MONO, fontSize: 12, color: "#64748b" };
const section: CSSProperties = { ...card, padding: "20px 22px", display: "flex", flexDirection: "column", gap: 16 };

/** The draft of one field as the form holds it: text for inputs, a boolean for switches, null = back to the environment. */
type Draft = Record<string, unknown>;

function toDraft(item: Item): unknown {
  if (item.kind === "bool") return item.value as boolean;
  if (item.kind === "domains") return ((item.value as string[] | null) ?? []).join(", ");
  return item.value == null ? "" : String(item.value);
}

function fromDraft(item: Item, value: unknown): unknown {
  if (value === null || item.kind === "bool") return value;
  if (item.kind === "int" || item.kind === "float") return value === "" ? NaN : Number(value);
  return value;
}

export default function Config() {
  const toast = useToast();
  const data = useLoad(() => api.get<Overview>("/api/admin/config"));
  const [params, setParams] = useSearchParams();
  const groups = data.data?.groups ?? [];
  const current = params.get("afsnit") ?? groups[0]?.key ?? "env";
  const group = groups.find((g) => g.key === current);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const select = (key: string) => {
    setError(null);
    setNotice(null);
    setParams(key === groups[0]?.key ? {} : { afsnit: key }, { replace: true });
  };

  const importEnv = async () => {
    setError(null);
    try {
      const r = await api.post<{ copied: string[]; skipped: { env: string; reason: string }[] }>("/api/admin/config/import");
      setNotice(`${r.copied.length} værdier er flyttet ind fra miljøet.` +
        (r.skipped.length ? ` Blev i miljøet: ${r.skipped.map((s) => `${s.env} (${s.reason})`).join("; ")}.` : ""));
      await data.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <PageTitle title="Konfiguration" icon={<SlidersHorizontal size={24} />}
        aside={data.data && data.data.importable > 0
          ? <Btn icon={Download} onClick={importEnv} deeplink="config-import">Importér {data.data.importable} fra miljøet</Btn> : undefined}>
        Indstillinger, der før kun kunne sættes som miljøvariabler. En værdi gemt her vinder over miljøet; uden den bruges
        miljøet og derefter standardværdien. Ændringer når alle containere inden for få sekunder.
      </PageTitle>
      <Alert kind="success">{notice}</Alert>
      <Alert>{error || data.error}</Alert>
      {!data.data ? <Loading /> : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 20, alignItems: "flex-start" }}>
          <nav aria-label="Afsnit" style={{ ...card, flex: "1 1 220px", maxWidth: 260, padding: 8, display: "flex", flexDirection: "column", gap: 2 }}>
            {groups.map((g) => (
              <SectionLink key={g.key} active={g.key === current} onClick={() => select(g.key)} label={g.label}
                count={g.items.length} />
            ))}
            <div style={{ borderTop: "1px solid #e2e8f0", margin: "6px 4px" }} />
            <SectionLink active={current === "env"} onClick={() => select("env")} label="Bliver i miljøet"
              count={data.data.env_only.length} />
          </nav>
          <div style={{ flex: "999 1 560px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
            {group ? (
              <GroupForm key={group.key} group={group}
                onSaved={async (r) => {
                  const parts = [`${r.changed.length + r.secrets_set.length + r.secrets_removed.length} ændringer er gemt.`];
                  if (r.secrets_cleared.length) parts.push(`Adressen skiftede vært, så disse skal tastes igen: ${names(group, r.secrets_cleared)}.`);
                  if (r.restart.length) parts.push(`Kræver genstart: ${names(group, r.restart)}.`);
                  setNotice(parts.join(" "));
                  setError(null);
                  toast("Konfigurationen er gemt");
                  await data.reload();
                }}
                onNotice={(text) => { setError(null); setNotice(text); }}
                onError={(e) => { setNotice(null); setError(e); }} />
            ) : <EnvOnlyList items={data.data.env_only} />}
          </div>
        </div>
      )}
    </div>
  );
}

function names(group: Group, keys: string[]): string {
  return keys.map((k) => group.items.find((i) => i.key === k)?.label ?? k).join(", ");
}

function SectionLink({ active, onClick, label: text, count }: { active: boolean; onClick: () => void; label: string; count: number }) {
  return (
    <button type="button" onClick={onClick} className={active ? undefined : "tg-hover"} aria-current={active ? "page" : undefined}
      style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, padding: "9px 10px", borderRadius: 8,
        border: 0, cursor: "pointer", textAlign: "left", font: `${active ? 600 : 400} 14px ${SANS}`,
        background: active ? "rgba(37,99,235,0.08)" : "transparent", color: active ? "#1d4ed8" : "#020817" }}>
      <span>{text}</span><span style={{ fontSize: 12, color: "#64748b" }}>{count}</span>
    </button>
  );
}

function GroupForm({ group, onSaved, onNotice, onError }: {
  group: Group; onSaved: (r: SaveResult) => void; onNotice: (text: string) => void; onError: (e: string) => void;
}) {
  const plain = group.items.filter((i) => !i.secret);
  const initial = useMemo(() => Object.fromEntries(plain.map((i) => [i.key, toDraft(i)])), [group]);
  const [draft, setDraft] = useState<Draft>(initial);
  const [secrets, setSecrets] = useState<Record<string, string | null>>({});
  const [saving, setSaving] = useState(false);
  useEffect(() => { setDraft(initial); setSecrets({}); }, [initial]);

  const providerKey = PROVIDER_KEY[group.key];
  const provider = providerKey ? String(draft[providerKey] ?? "") : null;
  const visible = (i: Item) => !i.provider || i.provider === provider;
  const changed = plain.filter((i) => draft[i.key] !== initial[i.key]);
  const dirty = changed.length > 0 || Object.keys(secrets).length > 0;

  const save = async () => {
    setSaving(true);
    try {
      const values = Object.fromEntries(changed.map((i) => [i.key, fromDraft(i, draft[i.key])]));
      const r = await api.put<SaveResult>(`/api/admin/config/${group.key}`, { values, secrets });
      onSaved(r);
    } catch (e) {
      onError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const testMail = async () => {
    try {
      const r = await api.post<{ sent_to: string }>("/api/admin/config/test-mail");
      onNotice(`Testmailen er sendt til ${r.sent_to}. Kig i indbakken.`);
    } catch (e) {
      onError(errorText(e));
    }
  };

  const shownSecrets = group.items.filter((i) => i.secret && visible(i));
  return (
    <>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "flex-end", gap: 12 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>{group.label}</h2>
          <p style={{ margin: 0, ...hint, fontSize: 14, maxWidth: 640 }}>{group.description}</p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {group.key === "mail" && <Btn icon={Mail} onClick={testMail} disabled={dirty} title={dirty ? "Gem først" : undefined}>Send testmail til mig</Btn>}
          <Btn kind="primary" onClick={save} disabled={!dirty || saving} deeplink={`config-save-${group.key}`}>{saving ? "Gemmer …" : "Gem ændringer"}</Btn>
        </div>
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 14px", borderRadius: 10, background: "rgba(37,99,235,0.06)",
        border: "1px solid rgba(37,99,235,0.2)", fontSize: 13, lineHeight: 1.5, color: "#1e3a8a" }}>
        <Lock size={16} style={{ flex: "none", marginTop: 2 }} />
        <span>Gem kræver en frisk anden faktor. Hver ændring skrives i audit-loggen med gammel og ny værdi; for hemmeligheder kun at de er ændret.</span>
      </div>

      <section style={section}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 300px), 1fr))", gap: "18px 20px" }}>
          {plain.filter(visible).map((i) => (
            <Field key={i.key} item={i} value={draft[i.key]} changed={draft[i.key] !== initial[i.key]}
              onChange={(v) => setDraft({ ...draft, [i.key]: v })} />
          ))}
        </div>
      </section>

      {shownSecrets.length > 0 && (
        <section style={section}>
          <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>Hemmeligheder</h3>
            <span style={hint}>Krypteret i databasen. Vises aldrig igen, når de er gemt.</span>
          </div>
          {shownSecrets.map((i) => (
            <SecretRow key={i.key} item={i} value={secrets[i.key]} endpointChanged={!!i.endpoint && draft[i.endpoint] !== initial[i.endpoint]}
              onChange={(v) => {
                const next = { ...secrets };
                if (v === undefined) delete next[i.key]; else next[i.key] = v;
                setSecrets(next);
              }} />
          ))}
        </section>
      )}
    </>
  );
}

function Field({ item, value, changed, onChange }: { item: Item; value: unknown; changed: boolean; onChange: (v: unknown) => void }) {
  const [text, tone] = SOURCE[item.source];
  const id = `cfg-${item.key}`;
  const back = value === null;
  const range = item.min != null && item.max != null ? `${fmt(item.min)}–${fmt(item.max)}` : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }} data-deeplink={`config-${item.key}`}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
        <label htmlFor={id} style={label}>{item.label}</label>
        {changed ? <Pill tone="dark">{back ? "Tilbage til miljøet" : "Ændret"}</Pill> : <Pill tone={tone}>{text}</Pill>}
      </div>
      {back ? (
        <div style={{ ...input, display: "flex", alignItems: "center", background: "#f8fafc", color: "#64748b" }}>
          {fmtValue(item.fallback)}
        </div>
      ) : item.kind === "bool" ? (
        <label style={{ display: "flex", alignItems: "center", gap: 10, height: 38, fontSize: 14 }}>
          <input id={id} type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} style={{ width: 18, height: 18 }} />
          {value ? "Ja" : "Nej"}
        </label>
      ) : item.kind === "enum" ? (
        <select id={id} value={String(value)} onChange={(e) => onChange(e.target.value)} style={input}>
          {item.options.map((o) => <option key={o} value={o} disabled={item.blocked.includes(o)}>{o}{item.blocked.includes(o) ? " (ikke i produktion)" : ""}</option>)}
        </select>
      ) : item.kind === "text" ? (
        <textarea id={id} value={String(value)} onChange={(e) => onChange(e.target.value)} rows={3} maxLength={item.max_len}
          style={{ ...input, height: "auto", padding: 10, fontFamily: MONO, fontSize: 12 }} />
      ) : (
        <input id={id} value={String(value)} onChange={(e) => onChange(e.target.value)} maxLength={item.max_len}
          type={item.kind === "int" || item.kind === "float" ? "number" : item.kind === "email" ? "email" : "text"}
          min={item.min ?? undefined} max={item.max ?? undefined} step={item.kind === "float" ? "any" : undefined}
          style={{ ...input, ...(item.kind === "url" ? { fontFamily: MONO, fontSize: 13 } : {}) }} />
      )}
      <span style={hint}>
        <span style={mono}>{item.env}</span>
        {range && ` · ${range}`}
        {item.help && ` · ${item.help}`}
        {item.restart && " · kræver genstart"}
      </span>
      {item.source === "db" && !back && (
        <button type="button" onClick={() => onChange(null)} className="tg-hover"
          style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 5, border: 0, background: "transparent",
            padding: "2px 4px", borderRadius: 6, cursor: "pointer", font: `500 12.5px ${SANS}`, color: "#1d4ed8" }}>
          <RotateCcw size={13} /> Brug miljøet ({fmtValue(item.fallback)})
        </button>
      )}
    </div>
  );
}

function SecretRow({ item, value, endpointChanged, onChange }: {
  item: Item; value: string | null | undefined; endpointChanged: boolean; onChange: (v: string | null | undefined) => void;
}) {
  const editing = typeof value === "string";
  const removing = value === null;
  const status = item.ignored
    ? <span style={{ color: "#9a3412" }}>Bruges ikke: {item.ignored}</span>
    : item.set
      ? <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#166534" }}>
          <CheckCircle2 size={15} />{item.source === "env" ? "Fra miljøet" : `Gemt${item.set_at ? ` · ${fmtDate(item.set_at)}` : ""}`}
        </span>
      : <span style={{ color: "#64748b" }}>Ikke sat</span>;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "12px 14px", border: "1px solid #e2e8f0", borderRadius: 10 }}
      data-deeplink={`config-${item.key}`}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 16px" }}>
        <div style={{ flex: "1 1 240px", display: "flex", flexDirection: "column", gap: 3 }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14, fontWeight: 600 }}><KeyRound size={14} />{item.label}</span>
          <span style={mono}>{item.env}</span>
        </div>
        <div style={{ flex: "1 1 200px", fontSize: 13.5 }}>
          {removing ? <span style={{ color: "#9a3412" }}>Fjernes ved gem{item.source === "db" ? "; miljøets værdi bruges, hvis der er en" : ""}</span>
            : endpointChanged && item.source === "db" ? <span style={{ color: "#9a3412" }}>Slettes ved gem: adressen skifter</span> : status}
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {editing || removing
            ? <Btn onClick={() => onChange(undefined)}>Fortryd</Btn>
            : <>
                <Btn onClick={() => onChange("")}>{item.set || item.source === "db" ? "Erstat" : "Tilføj"}</Btn>
                {item.source === "db" && <Btn kind="ghost" onClick={() => onChange(null)}>Fjern</Btn>}
              </>}
        </div>
      </div>
      {editing && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label htmlFor={`cfg-${item.key}`} style={label}>Ny værdi</label>
          <input id={`cfg-${item.key}`} type="password" autoComplete="off" value={value ?? ""} onChange={(e) => onChange(e.target.value)}
            style={{ ...input, fontFamily: MONO }} />
          {item.help && <span style={hint}>{item.help}</span>}
        </div>
      )}
    </div>
  );
}

function EnvOnlyList({ items }: { items: EnvOnly[] }) {
  const order: EnvOnly["category"][] = ["manual", "compose", "image", "optional", "web"];
  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700, display: "flex", alignItems: "center", gap: 8 }}><Server size={20} />Bliver i miljøet</h2>
        <p style={{ margin: 0, ...hint, fontSize: 14, maxWidth: 680 }}>
          Det, appen skal kende, før den kan læse sin database, og det, der aldrig må kunne ændres fra en browser. Værdierne
          vises aldrig her, kun om de er sat.
        </p>
      </div>
      {order.map((cat) => {
        const rows = items.filter((i) => i.category === cat);
        if (!rows.length) return null;
        const [title, why] = CATEGORY[cat];
        return (
          <section key={cat} style={section}>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>{title} · {rows.length}</h3>
              <span style={hint}>{why}</span>
            </div>
            <div style={{ display: "flex", flexDirection: "column" }}>
              {rows.map((r) => (
                <div key={r.env} style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: "4px 14px", padding: "9px 0", borderTop: "1px solid #f1f5f9" }}>
                  <span style={{ flex: "0 0 270px", fontFamily: MONO, fontSize: 12.5, fontWeight: 600, overflowWrap: "anywhere" }}>{r.env}</span>
                  <span style={{ flex: "1 1 240px", fontSize: 13, color: "#475569" }}>{r.reason}</span>
                  <Pill tone={r.set ? "green" : cat === "manual" ? "red" : "gray"}>{r.set ? "Sat" : "Ikke sat"}</Pill>
                </div>
              ))}
            </div>
          </section>
        );
      })}
    </>
  );
}

function fmt(n: number): string {
  return n.toLocaleString("da-DK");
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "tom";
  if (typeof v === "boolean") return v ? "ja" : "nej";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "tom";
  return typeof v === "number" ? fmt(v) : String(v);
}
