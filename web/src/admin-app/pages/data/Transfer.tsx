// /admin/data/transfer — Eksport og import (server/app/transfer.py, routes/admin_transfer.py). Only Fuld adgang.
// Eksport: choose areas and options, the job data.export builds the package, download it for 24 hours.
// Import: upload a package, choose areas and options, a dry run shows what would happen, then import (and undo for 7 days).
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { api, errorText } from "../../../api";
import { Alert, download, fmtBytes, fmtDate, fmtNumber, Loading, PageHeader, useLoad } from "../../ui";

type Area = {
  key: string; title: string; description: string; group: string; importable: boolean; personal: boolean;
  rows: number | null; bytes: number;
};
type AreasResp = {
  areas: Area[]; blobs: { n: number; bytes: number }; never: string[]; max_mb: number; download_hours: number;
  undo_days: number; migration: string | null;
};
type TableStat = {
  table: string; title: string; area: string; created: number; updated: number; unchanged: number; different: number;
  skipped: number; failed: number;
};
type Report = {
  dry_run: boolean; tables: TableStat[]; blocked: boolean; rolled_back?: boolean; changes?: number;
  totals: { created: number; updated: number; unchanged: number; different: number; skipped: number; failed: number; errors: number };
  differences: { table: string; title: string; key: string; fields: string[]; here?: Record<string, string>; package?: Record<string, string> }[];
  errors: { table: string; title: string; key: string; message: string }[];
  warnings: { table: string; title: string; message: string; count: number }[];
  undo?: { deleted: number; restored: number; failed: number };
};
type SummaryArea = { key: string; title: string; importable: boolean; personal: boolean; rows: number; notes: string[] };
type Manifest = {
  source?: string; exported_at?: string; app_version?: string; migration?: string | null; areas: string[];
  personal: string; blobs?: { count: number; bytes: number }; tables?: Record<string, { rows: number }>;
};
type Transfer = {
  id: number; kind: "export" | "import"; status: string; options: Record<string, unknown>; filename: string;
  size_bytes: number | null; manifest: Manifest | null; report: Report | null; error: string | null;
  created_by_name: string | null; created_at: string; finished_at: string | null; expires_at: string | null;
  undo_until: string | null; has_package: boolean; has_password: boolean; dry_run_current: boolean;
  summary?: { areas: SummaryArea[]; blobs: number; lookup_users: boolean } | null;
};
type ImportOptions = {
  areas: string[]; conflict: string; match: string; missing: string; on_error: string; keep_times: boolean; file_blobs: boolean;
};

const BASE = "/api/admin/transfer";
const BUSY = new Set(["queued", "running", "dry_running", "undoing"]);
const STATUS: Record<string, [string, string]> = {
  queued: ["Venter", "blue"], running: ["Kører", "blue"], ready: ["Klar", "green"], uploaded: ["Uploadet", ""],
  dry_running: ["Tørkører", "blue"], dry_ready: ["Tørkørt", "amber"], done: ["Importeret", "green"],
  failed: ["Fejlet", "red"], cancelled: ["Annulleret", ""], expired: ["Udløbet", ""], undoing: ["Fortryder", "blue"],
  undone: ["Fortrudt", ""],
};
const PERSONAL: Record<string, string> = { with: "Medtaget", pseudo: "Pseudonymiseret", without: "Udeladt" };
const CONFIG_PRESET = ["web", "meta", "settings", "features", "webhooks", "data", "mcp", "adoption"];
const PERSONAL_ONLY = ["users", "roles", "audit_log", "auth_log"];

function StatusPill({ status }: { status: string }) {
  const [label, tone] = STATUS[status] ?? [status, ""];
  return <span className={`pill ${tone}`}>{label}</span>;
}

function Seg<T extends string>({ value, options, onChange, label }: {
  value: T; options: [T, string][]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label}
      style={{ display: "inline-flex", flexWrap: "wrap", gap: 4, padding: 4, background: "#f1f5f9", borderRadius: 8, alignSelf: "flex-start" }}>
      {options.map(([v, text]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} onClick={() => onChange(v)}
          style={{ font: "inherit", fontSize: 14, fontWeight: 600, padding: "7px 14px", border: 0, borderRadius: 6, cursor: "pointer",
            background: value === v ? "#fff" : "transparent", color: value === v ? "#020817" : "#475569",
            boxShadow: value === v ? "0 1px 2px rgb(0 0 0 / .08)" : "none" }}>
          {text}
        </button>
      ))}
    </div>
  );
}

function Check({ checked, onChange, title, help, right, disabled, style }: {
  checked: boolean; onChange: () => void; title: ReactNode; help?: ReactNode; right?: ReactNode; disabled?: boolean; style?: CSSProperties;
}) {
  return (
    <label style={{ display: "flex", gap: 12, alignItems: "flex-start", padding: "12px 16px", cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.5 : 1, ...style }}>
      <input type="checkbox" checked={checked} onChange={onChange} disabled={disabled}
        style={{ width: 18, height: 18, margin: "2px 0 0", accentColor: "#2563eb", flex: "none", padding: 0 }} />
      <span style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{ fontWeight: 600, display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>{title}</span>
        {help && <span className="small muted">{help}</span>}
      </span>
      {right && <span className="small muted num" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{right}</span>}
    </label>
  );
}

function Radio({ name, value, current, onChange, title, help }: {
  name: string; value: string; current: string; onChange: (v: string) => void; title: string; help: string;
}) {
  const on = value === current;
  return (
    <label style={{ display: "flex", gap: 12, alignItems: "flex-start", padding: "11px 14px", borderRadius: 12, cursor: "pointer",
      border: `1px solid ${on ? "#2563eb" : "#e2e8f0"}`, background: on ? "#eff6ff" : "#fff" }}>
      <input type="radio" name={name} checked={on} onChange={() => onChange(value)}
        style={{ width: 18, height: 18, margin: "2px 0 0", accentColor: "#2563eb", flex: "none", padding: 0 }} />
      <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <span style={{ fontWeight: 600 }}>{title}</span>
        <span className="small muted">{help}</span>
      </span>
    </label>
  );
}

function Fieldset({ legend, children }: { legend: string; children: ReactNode }) {
  return (
    <fieldset className="card" style={{ margin: 0, minWidth: 0, gap: 10 }}>
      <legend style={{ fontSize: 15, fontWeight: 700, padding: "0 6px" }}>{legend}</legend>
      {children}
    </fieldset>
  );
}

/** Reloads a transfer every 2 seconds while its job runs. */
function usePoll(t: Transfer | null, reload: () => Promise<void>) {
  useEffect(() => {
    if (!t || !BUSY.has(t.status)) return;
    const id = setInterval(() => { reload(); }, 2000);
    return () => clearInterval(id);
  }, [t?.status, reload]);
}

export default function Transfer() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("fane") === "import" ? "import" : params.get("fane") === "historik" ? "historik" : "eksport";
  const go = (fane: string, extra: Record<string, string> = {}) => setParams({ ...(fane === "eksport" ? {} : { fane }), ...extra });

  return (
    <>
      <PageHeader eyebrow="/admin/data/transfer" title="Eksport og import">
        Flyt data mellem Gær-instanser, tag en læsbar kopi før en større ændring, eller giv en kunde deres data med.
        Hver eksport og import kører som et job og bliver skrevet i audit-loggen.
      </PageHeader>
      <div role="tablist" style={{ display: "flex", gap: 28, borderBottom: "1px solid #e2e8f0" }}>
        {([["eksport", "Eksport"], ["import", "Import"], ["historik", "Historik"]] as const).map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} type="button" onClick={() => go(k)}
            style={{ font: "inherit", fontSize: 15, fontWeight: 600, padding: "10px 4px", background: "none", border: 0, cursor: "pointer",
              color: tab === k ? "#020817" : "#64748b", borderBottom: `2px solid ${tab === k ? "#2563eb" : "transparent"}`, marginBottom: -1 }}>
            {label}
          </button>
        ))}
      </div>
      {tab === "eksport" && <ExportTab onStarted={() => go("historik")} />}
      {tab === "import" && <ImportTab id={params.get("id")} open={(id) => go("import", id ? { id: String(id) } : {})} />}
      {tab === "historik" && <History openImport={(id) => go("import", { id: String(id) })} />}
    </>
  );
}

// ── Export ──────────────────────────────────────────────────────────────────

function ExportTab({ onStarted }: { onStarted: () => void }) {
  const data = useLoad(() => api.get<AreasResp>(`${BASE}/areas`));
  const [sel, setSel] = useState<Set<string>>(new Set(["users", "groups", "web", "meta", "settings", "features", "webhooks", "data", "mcp"]));
  const [personal, setPersonal] = useState<"with" | "pseudo" | "without">("pseudo");
  const [format, setFormat] = useState<"json" | "csv">("json");
  const [since, setSince] = useState("90");
  const [blobs, setBlobs] = useState(false);
  const [encrypt, setEncrypt] = useState(true);
  const [password, setPassword] = useState("");
  const [filename, setFilename] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const areas = data.data?.areas ?? [];
  const blocked = (a: Area) => (personal === "without" && PERSONAL_ONLY.includes(a.key)) || (a.key === "roles" && !sel.has("users"));
  const chosen = areas.filter((a) => sel.has(a.key) && !blocked(a));
  const groups = useMemo(() => {
    const out: { label: string; items: Area[] }[] = [];
    for (const a of areas) {
      let g = out.find((x) => x.label === a.group);
      if (!g) out.push((g = { label: a.group, items: [] }));
      g.items.push(a);
    }
    return out;
  }, [areas]);
  const rows = chosen.reduce((n, a) => n + (a.rows ?? 0), 0);
  const bytes = chosen.reduce((n, a) => n + a.bytes, 0) + (blobs && sel.has("files") ? data.data?.blobs.bytes ?? 0 : 0);
  const anyPersonal = chosen.some((a) => a.personal);
  const toggle = (k: string) => setSel((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  const passwordOk = !encrypt || password.trim().length >= 10;

  const start = async () => {
    setError(null);
    setBusy(true);
    try {
      await api.post(`${BASE}/exports`, {
        areas: chosen.map((a) => a.key), personal, format, since_days: since === "all" ? null : Number(since),
        include_blobs: blobs && format === "json", password: encrypt ? password.trim() : null, filename,
      });
      onStarted();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  if (!data.data) return data.error ? <Alert>{data.error}</Alert> : <Loading />;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 24, alignItems: "flex-start" }}>
      <section style={{ flex: "999 1 560px", minWidth: 0, display: "flex", flexDirection: "column", gap: 20 }}>
        <div className="spread">
          <h3>Hvad skal med?</h3>
          <div className="row" style={{ "--gap": "6px" } as CSSProperties}>
            <button className="btn btn-outline btn-sm" onClick={() => setSel(new Set(CONFIG_PRESET))}>Kun konfiguration</button>
            <button className="btn btn-outline btn-sm" onClick={() => setSel(new Set(areas.map((a) => a.key)))}>Alt</button>
            <button className="btn btn-outline btn-sm" onClick={() => setSel(new Set())}>Ingen</button>
          </div>
        </div>
        {groups.map((g) => {
          const on = g.items.filter((a) => sel.has(a.key) && !blocked(a)).length;
          const all = on === g.items.length;
          return (
            <div key={g.label} className="table-wrap">
              <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 16px", background: "#f8fafc" }}>
                <strong>{g.label}</strong>
                <span className="pill">{on} af {g.items.length}</span>
                <button className="btn btn-ghost btn-sm" style={{ marginLeft: "auto" }}
                  onClick={() => setSel((s) => { const n = new Set(s); g.items.forEach((a) => (all ? n.delete(a.key) : n.add(a.key))); return n; })}>
                  {all ? "Fravælg alle" : "Vælg alle"}
                </button>
              </div>
              {g.items.map((a) => (
                <Check key={a.key} checked={sel.has(a.key) && !blocked(a)} onChange={() => toggle(a.key)} disabled={blocked(a)}
                  style={{ borderTop: "1px solid #e2e8f0" }}
                  title={<>{a.title}{a.personal && <span className="pill amber">Persondata</span>}
                    {!a.importable && <span className="pill">Kun arkiv</span>}</>}
                  help={blocked(a) && personal === "without" && PERSONAL_ONLY.includes(a.key) ? "Kan ikke komme med, når persondata udelades." : a.description}
                  right={a.rows === null ? null : <>{fmtNumber(a.rows)} rækker<br />{fmtBytes(a.bytes)}</>} />
              ))}
            </div>
          );
        })}

        <div className="card muted-bg">
          <strong>Kommer aldrig med i en eksport</strong>
          <span className="small">
            {data.data.never.join(" · ")}. Henvisninger til hemmeligheder (fx <code>hemmelighed: drift/ovh</code> i en
            API-kilde) beholdes, så de kan forbindes igen efter en import.
          </span>
        </div>

        <h3 style={{ marginTop: 8 }}>Optioner</h3>
        <div className="card" style={{ gap: 20 }}>
          <div className="stack" style={{ "--gap": "8px" } as CSSProperties}>
            <strong className="small">Persondata</strong>
            <Seg label="Persondata" value={personal} onChange={setPersonal}
              options={[["with", "Medtag"], ["pseudo", "Pseudonymisér"], ["without", "Udelad"]]} />
            <span className="small muted">
              {personal === "with" && "E-mail, navn og telefon kommer med, som de er. Brug det kun til en kunde eller en flytning, du selv står for."}
              {personal === "pseudo" && "E-mail, navn og telefon erstattes af faste koder (bruger-17@eksport.invalid). Relationer mellem rækkerne bevares. IP-adresser og detaljer i logs fjernes."}
              {personal === "without" && "Brugere og logs kommer ikke med, og rækker der tilhører en bruger (klip, hjemmemapper, medlemskaber) springes over."}
            </span>
          </div>
          <div className="stack" style={{ "--gap": "8px" } as CSSProperties}>
            <strong className="small">Format</strong>
            <Seg label="Format" value={format} onChange={setFormat} options={[["json", "Gær-pakke (.zip)"], ["csv", "CSV pr. tabel"]]} />
            <span className="small muted">
              {format === "json" ? "Én zip med manifest og én JSON-fil pr. tabel. Kan importeres i en anden Gær-instans."
                : "Én CSV-fil pr. tabel, til regneark og analyse. Kan ikke importeres igen."}
            </span>
          </div>
          <div className="grid" style={{ "--min": "240px" } as CSSProperties}>
            <label className="field">Periode for klip og logs
              <select value={since} onChange={(e) => setSince(e.target.value)}>
                <option value="30">Seneste 30 dage</option>
                <option value="90">Seneste 90 dage</option>
                <option value="365">Seneste 12 måneder</option>
                <option value="all">Alt</option>
              </select>
            </label>
            <label className="field">Filnavn
              <input value={filename} onChange={(e) => setFilename(e.target.value)} placeholder="gaer-eksport-ÅÅÅÅ-MM-DD" maxLength={120} />
            </label>
          </div>
          <div className="table-wrap" style={{ borderRadius: 12 }}>
            <Check checked={blobs && sel.has("files") && format === "json"} onChange={() => setBlobs(!blobs)}
              disabled={!sel.has("files") || format !== "json"}
              title="Medtag filernes indhold fra Object Storage"
              help={`Ellers kun mapper og navne. Pakken må højst fylde ${fmtNumber(data.data.max_mb)} MB.`}
              right={`+ ${fmtBytes(data.data.blobs.bytes)}`} />
          </div>
          <div className="table-wrap" style={{ borderRadius: 12 }}>
            <Check checked={encrypt} onChange={() => setEncrypt(!encrypt)} title="Kryptér pakken med en adgangskode"
              help="AES-256. Adgangskoden gemmes ikke, når pakken er bygget. Uden den kan pakken ikke læses eller importeres." />
          </div>
          {encrypt && (
            <label className="field" style={{ maxWidth: 380, marginTop: -8 }}>Adgangskode (mindst 10 tegn)
              <input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} maxLength={200} />
            </label>
          )}
        </div>
      </section>

      <aside aria-label="Opsummering" style={{ flex: "1 1 300px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16 }}>
        <div className="card" style={{ gap: 14 }}>
          <span className="eyebrow">Eksporten</span>
          <div className="grid" style={{ "--min": "100px" } as CSSProperties}>
            <div><div className="stat">{chosen.length}</div><span className="small muted">af {areas.length} områder</span></div>
            <div><div className="stat">{fmtNumber(rows)}</div><span className="small muted">rækker</span></div>
          </div>
          <div className="spread small" style={{ borderTop: "1px solid #e2e8f0", paddingTop: 12 }}>
            <span className="muted">Anslået størrelse</span><strong className="num">{fmtBytes(bytes)}</strong>
          </div>
          <div className="spread small"><span className="muted">Persondata</span><strong>{anyPersonal ? PERSONAL[personal] : "Ingen"}</strong></div>
          <div className="spread small"><span className="muted">Krypteret</span><strong>{encrypt ? "Ja, AES-256" : "Nej"}</strong></div>
          {anyPersonal && personal === "with" && (
            <Alert kind="warn">Pakken indeholder persondata i klar tekst. Opbevar den fortroligt, og slet den, når du er færdig.</Alert>
          )}
          <Alert>{error}</Alert>
          <button className="btn btn-primary btn-block" disabled={busy || !chosen.length || !passwordOk} onClick={start}>
            {busy ? "Starter …" : "Start eksport"}
          </button>
          <span className="tiny muted">
            Du bekræfter med passkey eller sikkerhedsnøgle. Pakken bygges som jobbet <code>data.export</code> og kan hentes
            i {data.data.download_hours} timer; derefter slettes den.
          </span>
        </div>
        <div className="alert info">
          <strong>Manifest</strong><br />
          Hver pakke har <code>manifest.json</code> med kilde, Gær-version, migrationsniveau, valgte områder, antal rækker og en
          kontrolsum pr. fil. Det er det, importen læser først.
        </div>
      </aside>
    </div>
  );
}

// ── Import ──────────────────────────────────────────────────────────────────

const CONFLICT: [string, string, string][] = [
  ["skip", "Spring over", "Det, der findes her, bliver stående. Sikreste valg."],
  ["overwrite", "Overskriv", "Rækken fra pakken erstatter den her. Den gamle version står i audit-loggen, og importen kan fortrydes."],
  ["merge", "Flet", "Kun tomme felter her udfyldes fra pakken. Intet bliver overskrevet."],
  ["copy", "Opret som kopi", "Rækken oprettes ved siden af med nyt navn, fx »priser-import« eller »Drift (importeret)«."],
];
const MATCH: [string, string, string][] = [
  ["natural", "Naturlig nøgle", "E-mail eller telefon for brugere, sti for filer og noder, slug for sider og grupper. Virker mellem instanser."],
  ["id", "Id fra kilden", "Kun når pakken kommer fra denne instans, fx en kopi taget før en ændring."],
];
const MISSING: [string, string, string][] = [
  ["create", "Opret dem", "Uden passkeys, second factor og admin-roller. De logger ind med engangskode som alle andre."],
  ["skip", "Spring deres rækker over", "Klip, filer og medlemskaber, der tilhører dem, kommer ikke med."],
  ["me", "Overfør til mig", "Rækkerne kommer med, og du står som ejer."],
];
const ON_ERROR: [string, string, string][] = [
  ["all", "Alt eller intet", "Hele importen er én transaktion. Én fejl, og intet bliver ændret."],
  ["continue", "Fortsæt og rapportér", "Rækker med fejl springes over og står i rapporten bagefter."],
];

function ImportTab({ id, open }: { id: string | null; open: (id: number | null) => void }) {
  return id ? <ImportDetail id={Number(id)} open={open} /> : <Upload open={open} />;
}

function Upload({ open }: { open: (id: number | null) => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recent = useLoad(() => api.get<{ items: Transfer[] }>(`${BASE}?kind=import&limit=10`));

  const send = async () => {
    if (!file) return;
    setError(null);
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("password", password);
      const t = await api.post<Transfer>(`${BASE}/imports`, body);
      open(t.id);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Steps current={1} />
      <div className="card" style={{ gap: 16, maxWidth: 640 }}>
        <h3>Upload en pakke</h3>
        <p className="small muted">
          En Gær-pakke (.zip eller krypteret .gaerx) fra Eksport. Pakken bliver læst og kontrolleret, men intet bliver
          importeret, før du har set en tørkørsel.
        </p>
        <label className="field">Pakke
          <input type="file" accept=".zip,.gaerx,application/zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        <label className="field">Adgangskode (hvis pakken er krypteret)
          <input type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} maxLength={200} />
        </label>
        <Alert>{error}</Alert>
        <div className="row">
          <button className="btn btn-primary" disabled={!file || busy} onClick={send}>{busy ? "Læser pakken …" : "Upload og læs pakken"}</button>
        </div>
      </div>
      {recent.data && recent.data.items.length > 0 && (
        <section className="stack">
          <h3>Seneste importer</h3>
          <TransferTable items={recent.data.items} onOpen={(t) => open(t.id)} />
        </section>
      )}
    </>
  );
}

function Steps({ current }: { current: number }) {
  const steps = ["Pakke", "Indhold og optioner", "Tørkørsel", "Importér"];
  return (
    <ol aria-label="Trin" style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexWrap: "wrap", gap: "8px 24px", fontSize: 14, fontWeight: 600 }}>
      {steps.map((s, i) => {
        const n = i + 1;
        const done = n < current;
        return (
          <li key={s} aria-current={n === current ? "step" : undefined}
            style={{ display: "flex", alignItems: "center", gap: 8, color: done ? "#166534" : n === current ? "#020817" : "#64748b" }}>
            <span aria-hidden="true" style={{ width: 26, height: 26, borderRadius: 9999, display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 13, background: done ? "#dcfce7" : n === current ? "#020817" : "#f1f5f9", color: n === current ? "#fff" : "inherit" }}>
              {done ? "✓" : n}
            </span>
            {s}
          </li>
        );
      })}
    </ol>
  );
}

function ImportDetail({ id, open }: { id: number; open: (id: number | null) => void }) {
  const t = useLoad(() => api.get<Transfer>(`${BASE}/${id}`), [id]);
  const meta = useLoad(() => api.get<AreasResp>(`${BASE}/areas`));
  const [opts, setOpts] = useState<ImportOptions | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  usePoll(t.data, t.reload);

  const tr = t.data;
  const importable = (tr?.summary?.areas ?? []).filter((a) => a.importable);
  useEffect(() => {
    if (!tr || opts) return;
    const saved = tr.options as Partial<ImportOptions>;
    setOpts({
      areas: saved.areas ?? importable.map((a) => a.key).filter((k) => k !== "roles"),
      conflict: saved.conflict ?? "skip", match: saved.match ?? "natural", missing: saved.missing ?? "create",
      on_error: saved.on_error ?? "all", keep_times: saved.keep_times ?? true, file_blobs: saved.file_blobs ?? true,
    });
  }, [tr, opts, importable]);

  if (!tr || !opts) return t.error ? <Alert>{t.error}</Alert> : <Loading />;
  const m = tr.manifest;
  const set = (patch: Partial<ImportOptions>) => setOpts({ ...opts, ...patch });
  const toggleArea = (k: string) => set({ areas: opts.areas.includes(k) ? opts.areas.filter((a) => a !== k) : [...opts.areas, k] });
  const report = tr.report;
  const sameOptions = tr.dry_run_current && JSON.stringify(sortKeys(opts)) === JSON.stringify(sortKeys(tr.options));
  const locked = BUSY.has(tr.status) || ["done", "undone", "failed", "expired"].includes(tr.status);
  const step = tr.status === "done" || tr.status === "undone" ? 5 : report && sameOptions ? 4 : 2;

  const call = async (fn: () => Promise<unknown>) => {
    setError(null);
    setBusy(true);
    try {
      await fn();
      await t.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const runDry = (o: ImportOptions = opts) => call(() => api.post(`${BASE}/${id}/dry-run`, o));
  const rows = importable.filter((a) => opts.areas.includes(a.key)).reduce((n, a) => n + a.rows, 0);

  return (
    <>
      <div className="spread">
        <Steps current={step} />
        <button className="btn btn-ghost btn-sm" onClick={() => open(null)}>Upload en anden pakke</button>
      </div>
      <div className="grid" style={{ "--min": "320px" } as CSSProperties}>
        <div className="card">
          <strong className="mono small" style={{ wordBreak: "break-all" }}>{tr.filename}</strong>
          <span className="small muted">
            {fmtBytes(tr.size_bytes)} · {tr.has_password ? "låst op med adgangskode · " : ""}kontrolsummer stemmer
            {m?.blobs?.count ? ` · ${fmtNumber(m.blobs.count)} filer med indhold` : ""}
          </span>
          <div className="row" style={{ "--gap": "6px" } as CSSProperties}>
            <StatusPill status={tr.status} />
            {m && <span className="pill">{PERSONAL[m.personal] ?? m.personal}</span>}
            {!tr.has_package && <span className="pill red">Pakken er slettet</span>}
          </div>
        </div>
        <div className="card" style={{ display: "grid", gridTemplateColumns: "max-content minmax(0, 1fr)", gap: "4px 16px", fontSize: 14 }}>
          <span className="muted">Kilde</span><span style={{ wordBreak: "break-all" }}>{m?.source ?? "—"}</span>
          <span className="muted">Eksporteret</span><span>{fmtDate(m?.exported_at)}</span>
          <span className="muted">Gær-version</span><span>{m?.app_version ?? "—"} · {m?.migration ?? "—"}</span>
          <span className="muted">Her</span>
          <span className="row" style={{ "--gap": "8px" } as CSSProperties}>
            {meta.data?.migration ?? "—"}
            {meta.data && m?.migration && (m.migration === meta.data.migration
              ? <span className="pill green">Samme niveau</span>
              : m.migration < (meta.data.migration ?? "") ? <span className="pill amber">Pakken er ældre</span>
                : <span className="pill red">Pakken er nyere</span>)}
          </span>
        </div>
      </div>
      {tr.error && <Alert>{tr.error}</Alert>}

      {tr.status === "done" || tr.status === "undone" || tr.status === "undoing" || tr.status === "running" ? (
        <Result tr={tr} undoDays={meta.data?.undo_days ?? 7} busy={busy} onUndo={() => {
          if (confirm("Fortryd importen? De rækker, den oprettede, slettes, og de, den ændrede, får deres gamle værdier igen.")) {
            call(() => api.post(`${BASE}/${id}/undo`));
          }
        }} />
      ) : (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 24, alignItems: "flex-start" }}>
            <section style={{ flex: "999 1 560px", minWidth: 0 }} className="stack">
              <h3>Hvad skal importeres?</h3>
              <div className="table-wrap">
                <table>
                  <thead><tr><th style={{ width: 36 }}><span style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)" }}>Vælg</span></th><th>Område</th><th className="num">I pakken</th><th>Bemærk</th></tr></thead>
                  <tbody>
                    {importable.map((a) => (
                      <tr key={a.key} style={{ opacity: opts.areas.includes(a.key) ? 1 : 0.55 }}>
                        <td><input type="checkbox" aria-label={a.title} checked={opts.areas.includes(a.key)} disabled={locked}
                          onChange={() => toggleArea(a.key)} style={{ width: 18, height: 18, accentColor: "#2563eb", padding: 0 }} /></td>
                        <td><strong>{a.title}</strong> {a.personal && <span className="pill amber">Persondata</span>}</td>
                        <td className="num">{a.key === "roles" ? "—" : fmtNumber(a.rows)}</td>
                        <td>{a.notes.map((n) => <span key={n} className="pill amber" style={{ marginRight: 4 }}>{n}</span>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {(tr.summary?.areas ?? []).some((a) => !a.importable) && (
                <span className="small muted">Logs i pakken kan ikke importeres; de er kun med til arkiv.</span>
              )}
              {tr.summary?.lookup_users && !opts.areas.includes("users") && (
                <span className="small muted">Pakken har de brugere, rækkerne tilhører, så de kan findes eller oprettes her.</span>
              )}
            </section>
            <aside className="card muted-bg" style={{ flex: "1 1 260px", minWidth: 0 }}>
              <span className="eyebrow">Valgt</span>
              <span className="stat">{fmtNumber(rows)} <span className="small muted" style={{ letterSpacing: 0 }}>rækker</span></span>
              <span className="small muted">{opts.areas.length} områder</span>
            </aside>
          </div>

          <h3>Optioner</h3>
          <div className="grid" style={{ "--min": "380px" } as CSSProperties}>
            <Fieldset legend="Når en række findes i forvejen">
              {CONFLICT.map(([v, title, help]) => <Radio key={v} name="conflict" value={v} current={opts.conflict} onChange={(x) => set({ conflict: x })} title={title} help={help} />)}
            </Fieldset>
            <div className="stack" style={{ "--gap": "20px" } as CSSProperties}>
              <Fieldset legend="Genkend rækker på">
                {MATCH.map(([v, title, help]) => <Radio key={v} name="match" value={v} current={opts.match} onChange={(x) => set({ match: x })} title={title} help={help} />)}
              </Fieldset>
              <Fieldset legend="Brugere som ikke findes her">
                {MISSING.map(([v, title, help]) => <Radio key={v} name="missing" value={v} current={opts.missing} onChange={(x) => set({ missing: x })} title={title} help={help} />)}
              </Fieldset>
            </div>
            <Fieldset legend="Ved fejl">
              {ON_ERROR.map(([v, title, help]) => <Radio key={v} name="onerror" value={v} current={opts.on_error} onChange={(x) => set({ on_error: x })} title={title} help={help} />)}
            </Fieldset>
            <Fieldset legend="Mere">
              <Check style={{ padding: "8px 0" }} checked={opts.keep_times} onChange={() => set({ keep_times: !opts.keep_times })}
                title="Bevar tidsstempler og forfattere"
                help="Oprettet- og ændret-tider og hvem der skrev rækken, som i kilden. Ellers står importen som forfatter." />
              <Check style={{ padding: "8px 0", borderTop: "1px solid #e2e8f0" }} checked={opts.file_blobs} onChange={() => set({ file_blobs: !opts.file_blobs })}
                title="Læg filernes indhold i Object Storage"
                help="Hvis pakken har det. Filer uden indhold i pakken springes over; eksisterende filer bliver stående." />
            </Fieldset>
          </div>
          <Alert kind="warn">
            Admin-roller importeres kun af admins med Fuld adgang, og aldrig på din egen konto. Webhooks og API-kilder kommer uden
            hemmeligheder: webhooks oprettes slået fra, og kilder skal have deres hemmeligheder tilknyttet i Hemmeligheder.
          </Alert>
          {report && <DryRun report={report} stale={!sameOptions} />}
          <Alert>{error}</Alert>
          <div className="row" style={{ borderTop: "1px solid #e2e8f0", paddingTop: 20 }}>
            {report && sameOptions && !report.blocked ? (
              <button className="btn btn-primary" disabled={busy || locked} onClick={() => {
                if (confirm(`Importér nu? ${fmtNumber(report.totals.created)} rækker oprettes og ${fmtNumber(report.totals.updated)} ændres.`)) {
                  call(() => api.post(`${BASE}/${id}/run`, opts));
                }
              }}>
                Importér {fmtNumber(report.totals.created + report.totals.updated)} rækker
              </button>
            ) : (
              <button className="btn btn-primary" disabled={busy || locked || !opts.areas.length || !tr.has_package} onClick={() => runDry()}>
                {tr.status === "dry_running" ? "Tørkører …" : report ? "Kør tørkørsel igen" : "Kør tørkørsel"}
              </button>
            )}
            {report && sameOptions && report.blocked && opts.on_error === "all" && (
              <button className="btn btn-outline" disabled={busy || locked} onClick={() => { const o = { ...opts, on_error: "continue" }; setOpts(o); runDry(o); }}>
                Spring de {fmtNumber(report.totals.errors)} rækker med fejl over
              </button>
            )}
            {report && sameOptions && !report.blocked && (
              <button className="btn btn-outline" disabled={busy || locked} onClick={() => runDry()}>Kør tørkørsel igen</button>
            )}
            <span className="small muted" style={{ flex: "1 1 280px" }}>
              {report && sameOptions && !report.blocked
                ? "Du bekræfter med passkey eller sikkerhedsnøgle. Importen kører som jobbet data.import."
                : "Tørkørslen ændrer ingenting. Den viser række for række, hvad importen vil gøre med de optioner, du har valgt."}
            </span>
          </div>
        </>
      )}
    </>
  );
}

function sortKeys(o: object): Record<string, unknown> {
  return Object.fromEntries(Object.entries(o).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, Array.isArray(v) ? [...v].sort() : v]));
}

function Tile({ label, value, tone }: { label: string; value: number; tone?: "green" | "blue" | "amber" | "red" }) {
  const colors: Record<string, [string, string, string]> = {
    green: ["#e2e8f0", "#fff", "#166534"], blue: ["#e2e8f0", "#fff", "#1e3a8a"],
    amber: ["#fde68a", "#fffbeb", "#92400e"], red: ["#fecaca", "#fef2f2", "#991b1b"],
  };
  const [border, bg, fg] = tone ? colors[tone] : ["#e2e8f0", "#fff", "#64748b"];
  return (
    <div style={{ border: `1px solid ${border}`, background: bg, borderRadius: 16, padding: "14px 18px", display: "flex", flexDirection: "column" }}>
      <span className="small" style={{ fontWeight: 600, color: fg }}>{label}</span>
      <span className="stat">{fmtNumber(value)}</span>
    </div>
  );
}

function DryRun({ report, stale }: { report: Report; stale: boolean }) {
  return (
    <section className="stack" style={{ "--gap": "16px", opacity: stale ? 0.55 : 1 } as CSSProperties}>
      <div className="spread">
        <h3>{report.dry_run ? "Sådan vil importen gå" : "Sådan gik importen"}</h3>
        {stale && <span className="pill amber">Optionerne er ændret: kør tørkørslen igen</span>}
      </div>
      <div className="grid" style={{ "--min": "150px", "--gap": "12px" } as CSSProperties}>
        <Tile label="Oprettes" value={report.totals.created} tone="green" />
        <Tile label="Opdateres" value={report.totals.updated} tone="blue" />
        <Tile label="Uændret" value={report.totals.unchanged} />
        <Tile label="Forskel, røres ikke" value={report.totals.different} tone="blue" />
        <Tile label="Springes over" value={report.totals.skipped} tone="amber" />
        <Tile label="Fejl" value={report.totals.errors} tone="red" />
      </div>
      {report.blocked && (
        <div role="alert" className="alert error">
          <strong>{fmtNumber(report.totals.errors)} {report.totals.errors === 1 ? "række har" : "rækker har"} fejl.</strong>{" "}
          Med Alt eller intet kan importen ikke køre, før de er rettet i pakken eller sprunget over.
        </div>
      )}
      <div className="table-wrap">
        <table style={{ minWidth: 720 }}>
          <thead><tr><th>Tabel</th><th className="num">Oprettes</th><th className="num">Opdateres</th><th className="num">Uændret</th>
            <th className="num">Forskel</th><th className="num">Sprunget over</th><th className="num">Fejl</th></tr></thead>
          <tbody>
            {report.tables.map((s) => (
              <tr key={s.table}>
                <td><strong>{s.title}</strong></td>
                <td className="num">{fmtNumber(s.created)}</td><td className="num">{fmtNumber(s.updated)}</td>
                <td className="num">{fmtNumber(s.unchanged)}</td><td className="num">{fmtNumber(s.different)}</td>
                <td className="num">{fmtNumber(s.skipped)}</td>
                <td className="num" style={s.failed ? { color: "#991b1b", fontWeight: 700 } : undefined}>{fmtNumber(s.failed)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 20, alignItems: "flex-start" }}>
        {report.differences.length > 0 && (
          <div className="card" style={{ flex: "3 1 460px", minWidth: 0, gap: 12 }}>
            <h3>Forskelle <span className="muted" style={{ fontWeight: 500 }}>· {fmtNumber(report.totals.different + report.totals.updated)}</span></h3>
            {report.differences.slice(0, 12).map((d, i) => (
              <div key={i} className="stack" style={{ "--gap": "6px", borderTop: i ? "1px solid #e2e8f0" : 0, paddingTop: i ? 12 : 0 } as CSSProperties}>
                <div className="row small" style={{ "--gap": "8px" } as CSSProperties}><span className="pill">{d.title}</span><code>{d.key}</code>
                  <span className="muted">{d.fields.join(", ")}</span></div>
                {d.here && d.package && (
                  <div className="grid small" style={{ "--min": "180px", "--gap": "8px" } as CSSProperties}>
                    <div style={{ background: "#f8fafc", border: "1px solid #e2e8f0", borderRadius: 8, padding: "8px 12px" }}>
                      <span className="tiny muted" style={{ fontWeight: 700, display: "block" }}>HER</span>
                      {Object.entries(d.here).map(([k, v]) => <div key={k}><code>{k}: {v}</code></div>)}
                    </div>
                    <div style={{ background: "#eff6ff", border: "1px solid #bfdbfe", borderRadius: 8, padding: "8px 12px" }}>
                      <span className="tiny" style={{ fontWeight: 700, display: "block", color: "#1e3a8a" }}>PAKKEN</span>
                      {Object.entries(d.package).map(([k, v]) => <div key={k}><code>{k}: {v}</code></div>)}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="stack" style={{ flex: "2 1 320px", minWidth: 0, "--gap": "20px" } as CSSProperties}>
          {report.errors.length > 0 && (
            <div className="card" style={{ borderColor: "#fecaca" }}>
              <h3>Fejl <span className="muted" style={{ fontWeight: 500 }}>· {fmtNumber(report.totals.errors)}</span></h3>
              {report.errors.slice(0, 20).map((e, i) => (
                <div key={i} className="small" style={{ borderTop: i ? "1px solid #e2e8f0" : 0, paddingTop: i ? 8 : 0 }}>
                  <strong>{e.title}</strong> <code>{e.key}</code><br /><span className="muted">{e.message}</span>
                </div>
              ))}
            </div>
          )}
          {report.warnings.length > 0 && (
            <div className="card" style={{ borderColor: "#fde68a" }}>
              <h3>Bemærk</h3>
              {report.warnings.map((w, i) => (
                <div key={i} className="small"><strong>{w.title}</strong>: {w.message} <span className="pill">{fmtNumber(w.count)}</span></div>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function Result({ tr, undoDays, busy, onUndo }: { tr: Transfer; undoDays: number; busy: boolean; onUndo: () => void }) {
  if (tr.status === "running") return <Alert kind="info">Importen kører. Siden opdaterer sig selv.</Alert>;
  const r = tr.report;
  return (
    <>
      {tr.status === "undoing" && <Alert kind="info">Importen bliver fortrudt. Siden opdaterer sig selv.</Alert>}
      {tr.status === "undone" && r?.undo && (
        <Alert kind="success">
          Importen er fortrudt: {fmtNumber(r.undo.deleted)} rækker slettet og {fmtNumber(r.undo.restored)} sat tilbage
          {r.undo.failed ? `, ${fmtNumber(r.undo.failed)} kunne ikke` : ""}.
        </Alert>
      )}
      {tr.status === "done" && (
        <Alert kind="success">
          Importen er kørt {fmtDate(tr.finished_at)}. Hver række står i audit-loggen, samlet under <code>transfer.imported</code>.
        </Alert>
      )}
      {r && <DryRun report={r} stale={false} />}
      {tr.status === "done" && (
        <div className="card muted-bg">
          <h3>Fortryd import</h3>
          {tr.undo_until ? (
            <>
              <span className="small">
                Indtil {fmtDate(tr.undo_until)} kan importen fortrydes: de rækker, den oprettede, slettes, og de, den ændrede,
                får deres gamle værdier igen. Ændringer, andre har lavet bagefter i de samme rækker, går tabt.
              </span>
              <div className="row"><button className="btn btn-danger" disabled={busy} onClick={onUndo}>Fortryd import</button></div>
            </>
          ) : (
            <span className="small muted">Importen ændrede ingenting, eller de {undoDays} dage er gået.</span>
          )}
        </div>
      )}
    </>
  );
}

// ── History ─────────────────────────────────────────────────────────────────

function TransferTable({ items, onOpen, actions }: {
  items: Transfer[]; onOpen?: (t: Transfer) => void; actions?: (t: Transfer) => ReactNode;
}) {
  return (
    <div className="table-wrap">
      <table style={{ minWidth: 760 }}>
        <thead><tr><th>Startet</th><th>Type</th><th>Af</th><th>Indhold</th><th>Persondata</th><th className="num">Størrelse</th><th>Status</th><th /></tr></thead>
        <tbody>
          {items.map((t) => {
            const areas = (t.kind === "export" ? (t.options.areas as string[]) : (t.manifest?.areas ?? [])) ?? [];
            const personal = t.kind === "export" ? String(t.options.personal ?? "") : t.manifest?.personal ?? "";
            return (
              <tr key={t.id}>
                <td style={{ whiteSpace: "nowrap" }}>{fmtDate(t.created_at)}</td>
                <td>{t.kind === "export" ? "Eksport" : "Import"}</td>
                <td>{t.created_by_name ?? "—"}</td>
                <td className="wrap">{t.filename}<br /><span className="tiny muted">{areas.length} områder</span></td>
                <td>{PERSONAL[personal] ?? "—"}</td>
                <td className="num">{t.size_bytes ? fmtBytes(t.size_bytes) : "—"}</td>
                <td><StatusPill status={t.status} />{t.error && <div className="tiny muted" style={{ maxWidth: 220 }}>{t.error}</div>}</td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {actions?.(t)}
                  {onOpen && t.kind === "import" && <button className="btn btn-outline btn-sm" onClick={() => onOpen(t)}>Åbn</button>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function History({ openImport }: { openImport: (id: number) => void }) {
  const list = useLoad(() => api.get<{ items: Transfer[] }>(`${BASE}?limit=100`));
  const [error, setError] = useState<string | null>(null);
  const busy = (list.data?.items ?? []).some((t) => BUSY.has(t.status));
  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => { list.reload(); }, 2000);
    return () => clearInterval(id);
  }, [busy, list.reload]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await list.reload();
    } catch (e) {
      setError(errorText(e));
    }
  };
  if (!list.data) return list.error ? <Alert>{list.error}</Alert> : <Loading />;
  return (
    <section className="stack">
      <Alert>{error}</Alert>
      {list.data.items.length === 0 ? <p className="muted">Ingen eksporter eller importer endnu.</p> : (
        <TransferTable items={list.data.items} onOpen={(t) => openImport(t.id)} actions={(t) => (
          <span className="row" style={{ "--gap": "6px", justifyContent: "flex-end", display: "inline-flex", marginRight: 6 } as CSSProperties}>
            {t.kind === "export" && t.status === "ready" && (
              <button className="btn btn-primary btn-sm" onClick={() => act(async () => download((await api.get<{ url: string }>(`${BASE}/${t.id}/download`)).url))}>
                Hent
              </button>
            )}
            {t.kind === "export" && (t.status === "queued" || t.status === "running") && (
              <button className="btn btn-outline btn-sm" onClick={() => act(() => api.post(`${BASE}/${t.id}/cancel`))}>Annullér</button>
            )}
            {t.has_package && !BUSY.has(t.status) && (
              <button className="btn btn-ghost btn-sm" onClick={() => {
                if (confirm("Slet pakken nu? Historikken bliver stående.")) act(() => api.del(`${BASE}/${t.id}/package`));
              }}>Slet pakke</button>
            )}
          </span>
        )} />
      )}
      <span className="small muted">
        En eksport kan hentes i 24 timer, og en uploadet pakke gemmes i 7 dage; derefter slettes pakken automatisk (jobbet
        data.transfer_cleanup). Historikken gemmes efter politikken »Eksport og import« i Jobs og oprydning.
      </span>
    </section>
  );
}
