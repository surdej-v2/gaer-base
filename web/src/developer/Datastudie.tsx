// /developer/datastudie — Datastudie (DS-3 … DS-9): the guide that turns a description into an MCP endpoint on
// /mcp/<slug> with tools built on operations, the endpoint's page with URL and tools, and the operations list.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errorText } from "../api";
import { Alert, fmtDate, useLoad } from "../ui";
import { DevPage, devState, useCrumb } from "./DeveloperShell";
import { ArtifactTabs, Cls, Empty, MASK_DA, StatusDot, Tabs, type ArtifactDetail } from "./devui";
import { copy } from "./parts";

type EndpointItem = { id: number; slug: string; name: string; version: string | null; status: string; statusLabel: string; url: string;
  audience: string; tools: number; owners: string[]; open: string | null };

export function EndpointList() {
  const { data, error } = useLoad(() => api.get<{ items: EndpointItem[] }>("/api/admin/dev/datastudie/endpoints"), []);
  const navigate = useNavigate();
  return (
    <DevPage title="Endpoints" intro="MCP-endpoints beskrevet i Datastudie. Hvert tool bygger på en operation, og maskeringen sker ét sted."
             actions={<Link to="/developer/datastudie/ny" className="btn btn-primary btn-sm">+ Nyt endpoint</Link>}>
      {error && <Alert>{error}</Alert>}
      {data && data.items.length === 0 && <Empty>Ingen endpoints endnu. <Link to="/developer/datastudie/ny">Start guiden</Link>.</Empty>}
      {data && data.items.length > 0 && (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Slug</th><th>Navn</th><th>Målgruppe</th><th>Version</th><th>Ejere</th><th>Tools</th><th>Status</th></tr></thead>
          <tbody>{data.items.map((e) => (
            <tr key={e.id} className="clickable" onClick={() => navigate(`/developer/datastudie/endpoints/${e.id}`)}>
              <td className="mono">/mcp/{e.slug}</td><td>{e.name}</td><td>{e.audience}</td><td className="mono">{e.version ?? "kladde"}</td>
              <td className="small">{e.owners.join(", ")}</td><td className="num">{e.tools}</td>
              <td><StatusDot status={e.status} label={e.statusLabel} /></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </DevPage>
  );
}

type Source = { id: number; name: string; relevance: string; why: string; kind: string; source: string; personal: boolean; refreshedAt: string | null; ai?: boolean };
type Field = { name: string; label: string; data_type: string; classification: string; personal_data: boolean; suggested: string; allowed: string[] };
type Choice = { dataset_id: number; name: string; fields: string[]; params: string[]; masking: Record<string, string>; level: string; tool: string; description: string };

const STEPS = ["Navn", "Beskrivelse", "Datakilder", "Detaljer", "Eksempler"];

export function Guide() {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [id, setId] = useState<number | null>(null);
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [instructions, setInstructions] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [all, setAll] = useState<{ id: number; name: string; kind: string; source: string }[]>([]);
  const [chosen, setChosen] = useState<Choice[]>([]);
  const [fields, setFields] = useState<Record<number, Field[]>>({});
  const [samples, setSamples] = useState<Record<number, { rows: Record<string, unknown>[]; columns: { name: string; policy: string }[] }>>({});
  const [err, setErr] = useState<string | null>(null);
  const ai = devState.me?.ai;

  const go = async (fn: () => Promise<void>) => {
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(errorText(e));
    }
  };
  const next1 = () => go(async () => {
    const r = await api.post<{ id: number }>("/api/admin/dev/datastudie/endpoints", { slug, name: name || slug });
    setId(r.id);
    setStep(2);
  });
  const next2 = () => go(async () => {
    await api.put(`/api/admin/dev/datastudie/endpoints/${id}`, { definition: { name: name || slug, description, instructions, audience: "internal", guide: { step: 3, description }, tools: [] } });
    const r = await api.post<{ items: Source[]; all: typeof all }>("/api/admin/dev/datastudie/sources", { description: description || name });
    setSources(r.items);
    setAll(r.all);
    setStep(3);
  });
  const toggle = (s: { id: number; name: string }) => setChosen((c) => c.some((x) => x.dataset_id === s.id) ? c.filter((x) => x.dataset_id !== s.id)
    : [...c, { dataset_id: s.id, name: s.name, fields: [], params: [], masking: {}, level: "white", tool: `soeg_${s.name.toLowerCase().replace(/[^a-z0-9]+/g, "_")}`.slice(0, 48), description: "" }]);
  const next3 = () => go(async () => {
    const f: Record<number, Field[]> = {};
    for (const c of chosen) f[c.dataset_id] = (await api.get<{ items: Field[] }>(`/api/admin/dev/datastudie/datasets/${c.dataset_id}/fields`)).items;
    setFields(f);
    setChosen((cs) => cs.map((c) => ({ ...c, fields: c.fields.length ? c.fields : f[c.dataset_id].map((x) => x.name) })));
    setStep(4);
  });
  const next4 = () => go(async () => {
    const out: typeof samples = {};
    for (const c of chosen) out[c.dataset_id] = await api.post("/api/admin/dev/datastudie/sample", { dataset_id: c.dataset_id, fields: c.fields, masking: c.masking });
    setSamples(out);
    setStep(5);
  });
  const send = () => go(async () => {
    await api.post(`/api/admin/dev/datastudie/endpoints/${id}/generate`, {
      description, instructions,
      sources: chosen.map((c) => ({ dataset_id: c.dataset_id, fields: c.fields, masking: c.masking, tool: c.tool, level: c.level,
        description: c.description || `Data fra ${c.name}`, params: c.params.map((p) => ({ name: p, field: p })) })) });
    await api.post(`/api/admin/dev/artifacts/${id}/submit`, {});
    devState.refresh();
    navigate(`/developer/datastudie/endpoints/${id}`);
  });
  const set = (dsid: number, patch: Partial<Choice>) => setChosen((cs) => cs.map((c) => (c.dataset_id === dsid ? { ...c, ...patch } : c)));

  return (
    <DevPage title="Nyt endpoint" intro="Guiden gemmes løbende som en kladde. Resultatet er et versioneret endpoint, der sendes til godkendelse.">
      <div className="dev-steps" aria-label="Trin">
        {STEPS.map((s, i) => <span key={s} className={`dev-step${step === i + 1 ? " on" : ""}`} aria-current={step === i + 1 ? "step" : undefined}>{i + 1} {s}</span>)}
      </div>
      {step === 1 && (
        <div className="dev-card">
          <strong>Hvad skal endpointet hedde?</strong>
          <input className="dev-input" placeholder="slug, fx lejedata" value={slug} onChange={(e) => setSlug(e.target.value.toLowerCase())} aria-label="Slug" />
          <input className="dev-input" placeholder="Navn" value={name} onChange={(e) => setName(e.target.value)} aria-label="Navn" />
          <span className="tiny muted">Udstilles på <span className="mono">/mcp/{slug || "…"}</span></span>
          <button className="btn btn-primary btn-sm" style={{ alignSelf: "flex-start" }} onClick={next1} disabled={slug.length < 2}>Næste: beskrivelse</button>
        </div>
      )}
      {step === 2 && (
        <div className="dev-card">
          <strong>Beskriv indholdet</strong>
          <textarea className="dev-input" style={{ height: 110, padding: 8 }} value={description} onChange={(e) => setDescription(e.target.value)}
                    placeholder="Hvad skal en AI-klient kunne slå op? Fx lejemål, årlig leje og lejere pr. ejendom." aria-label="Beskrivelse" />
          <textarea className="dev-input" style={{ height: 70, padding: 8 }} value={instructions} onChange={(e) => setInstructions(e.target.value)}
                    placeholder="Instruktioner til AI-klienten (valgfrit)" aria-label="Instruktioner" />
          <div className="row" style={{ gap: 8 }}><button className="btn btn-primary btn-sm" onClick={next2}>Næste: datakilder</button></div>
        </div>
      )}
      {step === 3 && (
        <div className="dev-card">
          <strong>Hvilke datakilder indeholder det?</strong>
          <span className="small muted">{ai ? "Forslag fra AI ud fra din beskrivelse og datakataloget" : "Forslag ud fra din beskrivelse og datakataloget (AI er slået fra)"}</span>
          {sources.map((s) => (
            <label key={s.id} className="dev-stage" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
              <input type="checkbox" checked={chosen.some((c) => c.dataset_id === s.id)} onChange={() => toggle(s)} />
              <span style={{ flex: 1 }}><strong>{s.name} ({s.source})</strong><br />
                <span className="tiny muted">{s.kind === "snapshot" ? `Snapshot fra Pipeline${s.refreshedAt ? `, genopfrisket ${fmtDate(s.refreshedAt)}` : ""}` : `Datasæt i kataloget${s.personal ? ", indeholder persondata" : ""}`}{s.why ? ` · ${s.why}` : ""}</span></span>
              <span className="small">{s.relevance}</span>
            </label>
          ))}
          <details><summary className="small">Alle datasæt</summary>
            {all.filter((a) => !sources.some((s) => s.id === a.id)).map((a) => (
              <label key={a.id} className="row small"><input type="checkbox" checked={chosen.some((c) => c.dataset_id === a.id)} onChange={() => toggle(a)} /> {a.name} <span className="muted">({a.source})</span></label>
            ))}
          </details>
          <div className="row" style={{ gap: 8 }}><button className="btn btn-ghost btn-sm" onClick={() => setStep(2)}>Tilbage</button>
            <button className="btn btn-primary btn-sm" onClick={next3} disabled={!chosen.length}>Næste: detaljer</button></div>
        </div>
      )}
      {step === 4 && chosen.map((c) => (
        <div key={c.dataset_id} className="dev-card">
          <strong>{c.name}</strong>
          <div className="row" style={{ gap: 8 }}>
            <input className="dev-input mono" value={c.tool} onChange={(e) => set(c.dataset_id, { tool: e.target.value })} aria-label="Toolnavn" />
            <select className="dev-select" value={c.level} onChange={(e) => set(c.dataset_id, { level: e.target.value })} aria-label="Niveau">
              <option value="white">Hvid · ingen godkendelse</option><option value="green">Grøn · interne</option>
              <option value="yellow">Gul · brugeren godkender</option><option value="red">Rød · bruger og godkender</option>
            </select>
          </div>
          <input className="dev-input" placeholder="Beskrivelse af toolet til AI-klienten" value={c.description} onChange={(e) => set(c.dataset_id, { description: e.target.value })} aria-label="Toolbeskrivelse" />
          <div className="dev-wrap"><table className="dev-table">
            <thead><tr><th>Med</th><th>Felt</th><th>Klassifikation</th><th>Parameter</th></tr></thead>
            <tbody>{(fields[c.dataset_id] ?? []).map((f) => (
              <tr key={f.name}>
                <td><input type="checkbox" checked={c.fields.includes(f.name)} aria-label={`Medtag ${f.name}`}
                           onChange={() => set(c.dataset_id, { fields: c.fields.includes(f.name) ? c.fields.filter((x) => x !== f.name) : [...c.fields, f.name] })} /></td>
                <td className="mono">{f.name}</td><td><Cls c={f.classification} suffix={f.personal_data ? " · persondata" : ""} /></td>
                <td>{f.suggested === "show" ? <input type="checkbox" checked={c.params.includes(f.name)} aria-label={`Parameter ${f.name}`}
                      onChange={() => set(c.dataset_id, { params: c.params.includes(f.name) ? c.params.filter((x) => x !== f.name) : [...c.params, f.name] })} /> : <span className="tiny muted">maskeret</span>}</td>
              </tr>
            ))}</tbody>
          </table></div>
        </div>
      ))}
      {step === 4 && <div className="row" style={{ gap: 8 }}><button className="btn btn-ghost btn-sm" onClick={() => setStep(3)}>Tilbage</button>
        <button className="btn btn-primary btn-sm" onClick={next4}>Næste: eksempler</button></div>}
      {step === 5 && chosen.map((c) => (
        <div key={c.dataset_id} className="dev-card">
          <strong>Eksempeldata og maskering · {c.name}</strong>
          <span className="small muted">Live opslag i kilden · forslag ud fra feltklassifikationen</span>
          <div className="dev-wrap"><table className="dev-table">
            <thead><tr><th>Felt</th><th>Klassifikation</th><th>Maskering</th><th>Eksempel</th></tr></thead>
            <tbody>{(fields[c.dataset_id] ?? []).filter((f) => c.fields.includes(f.name)).map((f) => (
              <tr key={f.name}>
                <td className="mono">{f.name}</td><td><Cls c={f.classification} suffix={f.personal_data ? " · persondata" : ""} /></td>
                <td><select className="dev-select" value={c.masking[f.name] ?? f.suggested} aria-label={`Maskering ${f.name}`}
                            onChange={(e) => set(c.dataset_id, { masking: { ...c.masking, [f.name]: e.target.value } })}>
                  {f.allowed.map((m) => <option key={m} value={m}>{MASK_DA[m]}{m === f.suggested ? " (forslag)" : ""}</option>)}
                </select></td>
                <td className="small">{String(samples[c.dataset_id]?.rows[0]?.[f.name] ?? "")}</td>
              </tr>
            ))}</tbody>
          </table></div>
        </div>
      ))}
      {step === 5 && <div className="row" style={{ gap: 8 }}><button className="btn btn-ghost btn-sm" onClick={() => setStep(4)}>Tilbage</button>
        <button className="btn btn-outline btn-sm" onClick={next4}>Opdatér eksempler</button>
        <button className="btn btn-primary btn-sm" onClick={send}>Send til godkendelse</button></div>}
      {err && <Alert>{err}</Alert>}
    </DevPage>
  );
}

type Tool = { name: string; description: string; level: string; operation: number; operation_slug: string; fieldLevel: string; enabled: boolean };
type EndpointDetail = { artifact: ArtifactDetail; definition: { name: string; description: string; instructions: string; audience: string };
  tools: Tool[]; url: string; live: boolean };
type LogRow = { ts: string; method: string; tool: string | null; status: string; ms: number | null; who: string | null };

export function EndpointPage() {
  const { id } = useParams();
  const { data, error, setData } = useLoad(() => api.get<EndpointDetail>(`/api/admin/dev/datastudie/endpoints/${id}`), [id]);
  const [tab, setTab] = useState("tools");
  const [copied, setCopied] = useState(false);
  const [log, setLog] = useState<LogRow[] | null>(null);
  useCrumb(data?.artifact.slug);
  useEffect(() => {
    if (tab === "log" && data) api.get<{ items: LogRow[] }>(`/api/admin/studio/mcp-endpoints/${data.artifact.slug}/log`).then((r) => setLog(r.items)).catch(() => setLog([]));
  }, [tab, data]);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;
  const a = data.artifact;
  return (
    <DevPage title={<>{a.slug} <span className="pill mono">v{a.version ?? "—"}</span></>} intro={data.definition.description || a.name}
             actions={<StatusDot status={a.status} label={a.statusLabel} />}>
      <div className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
        <span className="small muted">MCP-URL</span>
        <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
          <input className="dev-input mono" style={{ flex: 1 }} readOnly value={data.url} aria-label="MCP-URL" />
          <button className="btn btn-primary btn-sm" onClick={async () => setCopied(await copy(data.url))}>{copied ? "Kopieret" : "Kopiér"}</button>
        </div>
        <span className="tiny muted">Tilføj URL'en som connector i Claude eller en anden MCP-klient. Klienten logger ind med OAuth mod denne instans.{!data.live && " Endpointet er ikke aktivt endnu."}</span>
      </div>
      <Tabs tabs={[["tools", "Tools"], ["owners", "Ejere og versioner"], ["log", "Log"]]} value={tab} onChange={setTab} />
      {tab === "tools" && (
        <div className="stack">
          {data.tools.length === 0 ? <Empty>Ingen tools endnu.</Empty> : (
            <div className="dev-wrap"><table className="dev-table">
              <thead><tr><th>Tool</th><th>Beskrivelse</th><th>Niveau</th><th>Felter</th><th>Type</th></tr></thead>
              <tbody>{data.tools.map((t) => (
                <tr key={t.name}><td className="mono"><strong>{t.name}</strong></td><td className="small">{t.description}</td>
                  <td><Cls c={t.level} /></td><td><Cls c={t.fieldLevel} /></td><td>Læser · <Link to={`/developer/artefakter/${t.operation}`} className="small">{t.operation_slug}</Link></td></tr>
              ))}</tbody>
            </table></div>
          )}
          <div className="row" style={{ gap: 8 }}>
            <Link to={`/developer/mcp/testbaenk?endpoint=${a.slug}`} className="btn btn-primary btn-sm">Test i MCP-studie</Link>
          </div>
        </div>
      )}
      {tab === "owners" && <ArtifactTabs artifact={a} onChange={(x) => setData({ ...data, artifact: x })} />}
      {tab === "log" && (log === null ? null : log.length === 0 ? <Empty>Ingen kald endnu.</Empty> : (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Tid</th><th>Metode</th><th>Tool</th><th>Hvem</th><th>Status</th><th>ms</th></tr></thead>
          <tbody>{log.map((l, i) => <tr key={i}><td className="small">{fmtDate(l.ts)}</td><td className="mono small">{l.method}</td><td className="mono small">{l.tool}</td>
            <td>{l.who}</td><td><StatusDot status={l.status === "ok" ? "ok" : "failed"} label={l.status} /></td><td className="num">{l.ms}</td></tr>)}</tbody>
        </table></div>
      ))}
    </DevPage>
  );
}

type Op = { id: number; slug: string; name: string; version: string | null; status: string; statusLabel: string; dataset: { name: string; kind: string } | null;
  fields: number; params: string[]; usedBy: { tool: string; endpoint: string }[] };

export function OperationList() {
  const { data, error } = useLoad(() => api.get<{ items: Op[] }>("/api/admin/dev/operations"), []);
  const navigate = useNavigate();
  return (
    <DevPage title="Operationer" intro="En operation er én forespørgsel med kilde, parametre, filtre, felter og maskering. MCP-tools og API-operationer bygger på den samme.">
      {error && <Alert>{error}</Alert>}
      {data && data.items.length === 0 && <Empty>Ingen operationer endnu. De laves af guiden i Datastudie.</Empty>}
      {data && data.items.length > 0 && (
        <div className="dev-wrap"><table className="dev-table">
          <thead><tr><th>Operation</th><th>Kilde</th><th>Felter</th><th>Parametre</th><th>Bruges af</th><th>Version</th><th>Status</th></tr></thead>
          <tbody>{data.items.map((o) => (
            <tr key={o.id} className="clickable" onClick={() => navigate(`/developer/datastudie/operationer/${o.id}`)}>
              <td className="mono">{o.slug}</td><td>{o.dataset?.name ?? "—"}</td><td className="num">{o.fields}</td>
              <td className="mono small">{o.params.join(", ") || "—"}</td><td className="small">{o.usedBy.map((u) => `/mcp/${u.endpoint} · ${u.tool}`).join(", ") || "—"}</td>
              <td className="mono">{o.version ?? "—"}</td><td><StatusDot status={o.status} label={o.statusLabel} /></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </DevPage>
  );
}

export function OperationPage() {
  const { id } = useParams();
  const { data, error, setData } = useLoad(() => api.get<ArtifactDetail>(`/api/admin/dev/operations/${id}`), [id]);
  const [args, setArgs] = useState("{}");
  const [draft, setDraft] = useState(false);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useCrumb(data?.slug);
  if (error) return <Alert>{error}</Alert>;
  if (!data) return null;
  const run = async () => {
    setErr(null);
    try {
      setResult(await api.post(`/api/admin/dev/operations/${id}/try`, { args: JSON.parse(args), draft }));
    } catch (e) {
      setErr(e instanceof SyntaxError ? "Argumenterne er ikke gyldig JSON" : errorText(e));
    }
  };
  const clear = async () => {
    await api.post(`/api/admin/dev/operations/${id}/cache/clear`);
    setResult(null);
  };
  const tryBox = (
    <div className="stack">
      <div className="row" style={{ gap: 8 }}>
        <input className="dev-input mono" style={{ flex: 1 }} value={args} onChange={(e) => setArgs(e.target.value)} aria-label="Argumenter (JSON)" />
        <label className="row small"><input type="checkbox" checked={draft} onChange={(e) => setDraft(e.target.checked)} /> Mod kladden</label>
        <button className="btn btn-primary btn-sm" onClick={run}>Kør</button>
        <button className="btn btn-outline btn-sm" onClick={clear}>Ryd cache</button>
      </div>
      {err && <Alert>{err}</Alert>}
      {result && <pre className="dev-code">{JSON.stringify(result, null, 2)}</pre>}
      <pre className="dev-code">{JSON.stringify(Object.fromEntries(Object.entries((data.draft ?? data.current)?.definition ?? {}).filter(([k]) => !k.startsWith("_"))), null, 2)}</pre>
    </div>
  );
  return (
    <DevPage title={<span className="mono">{data.slug}</span>} intro={`${data.name} · v${data.version ?? "—"}`} actions={<StatusDot status={data.status} label={data.statusLabel} />}>
      <ArtifactTabs artifact={data} onChange={setData} extra={[["try", "Prøv", tryBox]]} />
    </DevPage>
  );
}

type CacheStats = { backend: string; entries: number; live: number; bytes: number; hits: number; max_entries: number };

export function CachePage() {
  const { data } = useLoad(() => api.get<CacheStats>("/api/admin/dev/cache"), []);
  return (
    <DevPage title="Cache" intro="Opslag i live-kilder, operationer og multisource-kilder caches med TTL pr. kilde og trin. Værdierne krypteres i applikationen, og kun maskerede data caches.">
      {data && (
        <div className="dev-cards">
          <div className="dev-card"><span className="small muted">Lager</span><strong>{data.backend === "redis" ? "Redis (REDIS_URL)" : "Postgres (uden Redis)"}</strong></div>
          <div className="dev-card"><span className="small muted">Poster</span><strong>{data.live} aktive af {data.entries}</strong><span className="tiny muted">højst {data.max_entries}; de mindst brugte fjernes først</span></div>
          <div className="dev-card"><span className="small muted">Træf</span><strong>{data.hits}</strong></div>
        </div>
      )}
      <span className="small muted">Standard-TTL er 5 minutter for live-kilder, højst 1 time for delegerede og til næste genopfriskning for snapshots. Ryd cachen for en operation fra dens side.</span>
    </DevPage>
  );
}
