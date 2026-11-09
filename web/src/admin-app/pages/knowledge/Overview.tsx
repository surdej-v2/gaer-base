// /admin/knowledge → Viden: tal, vidensrum, agenter og papirkurven (specs/knowledge, canvas "Knowledge" → Admin).
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Bot, Library, Plus, RotateCcw, Trash2, Upload } from "lucide-react";
import { api, errorText } from "../../../api";
import { Btn, Empty, ErrorBox, Modal, PageTitle, Pill, useLoad, useToast } from "../../ui";
import { card, fmt, input, label, MONO, sectionLabel } from "../../theme";
import { initialsOf, version, type Overview as OverviewData } from "./types";

export default function Overview() {
  const { data, error, reload } = useLoad(() => api.get<OverviewData>("/api/admin/knowledge/overview"), []);
  const [creating, setCreating] = useState<"space" | "agent" | null>(null);
  const toast = useToast();

  const stat = (title: string, value: string, sub: ReactNode) => (
    <div style={{ ...card, padding: "16px 18px", display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: 12, color: "#475569" }}>{title}</span>
      <span style={{ fontSize: 26, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>{value}</span>
      <span style={{ fontSize: 12, color: "#475569" }}>{sub}</span>
    </div>
  );

  const docs = data?.spaces.reduce((n, s) => n + s.documents, 0) ?? 0;
  const indexed = data?.spaces.reduce((n, s) => n + s.indexed, 0) ?? 0;

  return (
    <>
      <PageTitle title="Viden" aside={
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Btn icon={Bot} onClick={() => setCreating("agent")}>Ny agent</Btn>
          <Btn kind="primary" icon={Plus} onClick={() => setCreating("space")}>Nyt vidensrum</Btn>
        </div>
      }>
        Upload dokumenter til et vidensrum, og byg agenter, der kun svarer ud fra dem — med kildehenvisninger. Hver udgivet agent får sin egen rute under /extension/agent.
      </PageTitle>
      {error && <ErrorBox>{error}</ErrorBox>}
      {data && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
            {stat("Vidensrum", fmt(data.spaces.length), `${data.bin.length} i papirkurven`)}
            {stat("Dokumenter", fmt(docs), `${fmt(indexed)} indekseret`)}
            {stat("Afsnit i indekset", fmt(data.stats.chunks), data.ai.embeddings ? `vektorer med ${data.ai.embed_model}` : "fuldtekst-søgning")}
            {stat("Spørgsmål, 30 dage", fmt(data.stats.questions_30d), `${fmt(data.stats.conversations_30d)} samtaler`)}
          </div>
          {!data.ai.enabled && (
            <div style={{ ...card, padding: "12px 16px", fontSize: 13, color: "#334155", background: "#f8fafc" }}>
              AI er ikke slået til (<code style={{ fontFamily: MONO }}>AI_API_KEY</code>). Agenterne søger med fuldtekst og svarer med de mest relevante uddrag i stedet for en formuleret tekst.
            </div>
          )}

          <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <span style={sectionLabel}>Vidensrum</span>
            {data.spaces.length === 0 ? (
              <div style={card}><Empty>Ingen vidensrum endnu. Opret et, og upload filer eller en ZIP.</Empty></div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12 }}>
                {data.spaces.map((s) => (
                  <Link key={s.id} to={`/knowledge/spaces/${s.id}`} style={{ ...card, padding: 18, display: "flex", flexDirection: "column", gap: 10,
                    textDecoration: "none", color: "#020817" }} className="tg-hover">
                    <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
                      <span style={{ width: 38, height: 38, borderRadius: 10, background: "#eff6ff", color: "#1d4ed8", display: "flex",
                        alignItems: "center", justifyContent: "center", fontWeight: 700, fontSize: 13 }}>{initialsOf(s.name)}</span>
                      <span style={{ flex: 1, minWidth: 0, fontWeight: 600, fontSize: 15 }}>{s.name}</span>
                      {s.failed > 0 ? <Pill tone="red">{s.failed} fejl</Pill>
                        : s.working > 0 ? <Pill tone="blue">Indekserer {s.working}</Pill>
                        : s.documents > 0 ? <Pill tone="green">Indekseret</Pill> : <Pill tone="gray">Tomt</Pill>}
                    </span>
                    {s.description && <span style={{ fontSize: 13, color: "#334155" }}>{s.description}</span>}
                    <span style={{ display: "flex", gap: 14, fontSize: 12, color: "#475569" }}>
                      <span>{fmt(s.documents)} dokumenter</span><span>{s.agents} agenter</span>
                    </span>
                  </Link>
                ))}
              </div>
            )}
          </section>

          <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <span style={sectionLabel}>Agenter</span>
            <div style={{ ...card, overflowX: "auto" }}>
              {data.agents.length === 0 ? <Empty>Ingen agenter endnu.</Empty> : (
                <table style={{ width: "100%", minWidth: 640, borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ textAlign: "left", color: "#475569", fontSize: 11, letterSpacing: "0.05em", textTransform: "uppercase" }}>
                      <th style={{ padding: "10px 16px", fontWeight: 600 }}>Agent</th><th style={{ padding: "10px 16px", fontWeight: 600 }}>Status</th>
                      <th style={{ padding: "10px 16px", fontWeight: 600 }}>Vidensrum</th><th style={{ padding: "10px 16px", fontWeight: 600 }}>Rute</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.agents.map((a) => (
                      <tr key={a.id} style={{ borderTop: "1px solid #f1f5f9" }}>
                        <td style={{ padding: "12px 16px" }}><Link to={`/knowledge/agents/${a.id}`} style={{ fontWeight: 600, color: "#020817" }}>{a.name}</Link>
                          {a.access === "members" && <span style={{ color: "#475569" }}> · kun medlemmer</span>}</td>
                        <td style={{ padding: "12px 16px" }}>{a.status === "published" ? <Pill tone="green">Udgivet {version(a)}</Pill> : <Pill tone="gray">Kladde</Pill>}</td>
                        <td style={{ padding: "12px 16px", color: "#334155" }}>
                          {a.space_ids.map((id) => data.spaces.find((s) => s.id === id)?.name).filter(Boolean).join(", ") || "—"}</td>
                        <td style={{ padding: "12px 16px" }}>{a.status === "published"
                          ? <a href={`/extension/agent/${a.slug}`} target="_blank" rel="noreferrer" style={{ fontFamily: MONO, fontSize: 12 }}>/extension/agent/{a.slug}</a>
                          : <span style={{ color: "#475569" }}>Ikke udgivet</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>

          {data.bin.length > 0 && (
            <section style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <span style={sectionLabel}>Papirkurv · slettes efter {data.bin_days} dage</span>
              <div style={{ ...card, display: "flex", flexDirection: "column" }}>
                {data.bin.map((s) => {
                  const left = Math.max(0, data.bin_days - Math.floor((Date.now() - new Date(s.deleted_at!).getTime()) / 86_400_000));
                  return (
                    <div key={s.id} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, padding: "12px 16px", borderTop: "1px solid #f1f5f9" }}>
                      <span style={{ flex: "1 1 220px", display: "flex", flexDirection: "column" }}>
                        <b style={{ fontWeight: 600 }}>{s.name}</b><span style={{ fontSize: 12, color: "#475569" }}>{s.documents} dokumenter</span>
                      </span>
                      <Pill tone={left <= 5 ? "orange" : "gray"}>{left} dage tilbage</Pill>
                      <Btn icon={RotateCcw} onClick={async () => {
                        try { await api.post(`/api/admin/knowledge/spaces/${s.id}/restore`); toast("Vidensrummet er gendannet"); reload(); }
                        catch (e) { toast(errorText(e)); }
                      }}>Gendan</Btn>
                      <Btn icon={Trash2} style={{ color: "#b91c1c", borderColor: "#fecaca" }} onClick={async () => {
                        if (!confirm(`Slet „${s.name}“ endeligt? Alle filer, indekset og loggen fjernes, og det kan ikke fortrydes.`)) return;
                        try { await api.del(`/api/admin/knowledge/spaces/${s.id}/purge`); toast("Slettet endeligt"); reload(); }
                        catch (e) { toast(errorText(e)); }
                      }}>Slet endeligt</Btn>
                    </div>
                  );
                })}
              </div>
            </section>
          )}
        </>
      )}
      {creating && <CreateDialog kind={creating} spaces={data?.spaces ?? []} onClose={() => setCreating(null)} />}
    </>
  );
}

function CreateDialog({ kind, spaces, onClose }: { kind: "space" | "agent"; spaces: { id: number; name: string }[]; onClose: () => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [spaceIds, setSpaceIds] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const save = async () => {
    try {
      if (kind === "space") {
        const r = await api.post<{ id: number }>("/api/admin/knowledge/spaces", { name, description });
        navigate(`/knowledge/spaces/${r.id}`);
      } else {
        const r = await api.post<{ id: number }>("/api/admin/knowledge/agents", { name, space_ids: spaceIds });
        navigate(`/knowledge/agents/${r.id}`);
      }
    } catch (e) {
      setError(errorText(e));
    }
  };
  return (
    <Modal onClose={onClose} width={480}>
      <h2 style={{ margin: 0, fontSize: 18, display: "flex", alignItems: "center", gap: 8 }}>
        {kind === "space" ? <><Library size={18} /> Nyt vidensrum</> : <><Bot size={18} /> Ny agent</>}
      </h2>
      <label style={label}>Navn<input autoFocus value={name} onChange={(e) => setName(e.target.value)} style={input}
        placeholder={kind === "space" ? "fx Personalehåndbog" : "fx Personale-assistent"} /></label>
      {kind === "space" ? (
        <label style={label}>Beskrivelse<textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3}
          style={{ ...input, height: "auto", padding: 10 }} /></label>
      ) : (
        <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 6 }}>
          <legend style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Vidensrum</legend>
          {spaces.length === 0 && <span style={{ fontSize: 13, color: "#475569" }}>Opret et vidensrum først — eller vælg senere.</span>}
          {spaces.map((s) => (
            <label key={s.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14, minHeight: 32 }}>
              <input type="checkbox" checked={spaceIds.includes(s.id)}
                onChange={(e) => setSpaceIds(e.target.checked ? [...spaceIds, s.id] : spaceIds.filter((x) => x !== s.id))} />{s.name}
            </label>
          ))}
        </fieldset>
      )}
      {error && <ErrorBox>{error}</ErrorBox>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn onClick={onClose}>Annullér</Btn>
        <Btn kind="primary" icon={kind === "space" ? Upload : Bot} disabled={!name.trim()} onClick={save}>Opret</Btn>
      </div>
    </Modal>
  );
}
