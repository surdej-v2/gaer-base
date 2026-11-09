// /admin/knowledge/agents/:id → Agent: bygger (identitet, rute, instruktioner, vidensrum, startspørgsmål, adgang),
// medlemmer, udgivelse og versioner, og "Prøv agenten" mod arbejdskopien.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ExternalLink, History, Play, Plus, Rocket, Save, Send, Trash2, X } from "lucide-react";
import { api, errorText } from "../../../api";
import { Btn, ErrorBox, IconBtn, Modal, PageTitle, Pill, fmtDate, useLoad, useToast } from "../../ui";
import { card, input, label, MONO, sectionLabel, select } from "../../theme";
import { srOnly, version, type Agent as AgentData, type Answer, type Overview } from "./types";

type Draft = Pick<AgentData, "name" | "slug" | "description" | "instructions" | "welcome" | "suggested_prompts" | "space_ids" | "access">;

const textarea = { ...input, height: "auto", padding: 10, lineHeight: "20px", resize: "vertical" as const };

export default function Agent() {
  const { id } = useParams();
  const { data, error, reload } = useLoad(() => api.get<AgentData>(`/api/admin/knowledge/agents/${id}`), [id]);
  const spaces = useLoad(() => api.get<Overview>("/api/admin/knowledge/overview").then((o) => o.spaces), []);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [newPrompt, setNewPrompt] = useState("");
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const toast = useToast();
  const navigate = useNavigate();

  useEffect(() => {
    if (data) setDraft({ name: data.name, slug: data.slug, description: data.description, instructions: data.instructions,
      welcome: data.welcome, suggested_prompts: data.suggested_prompts, space_ids: data.space_ids, access: data.access });
  }, [data]);

  if (error) return <ErrorBox>{error}</ErrorBox>;
  if (!data || !draft) return null;
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft({ ...draft, [k]: v });
  const dirty = JSON.stringify(draft) !== JSON.stringify({ name: data.name, slug: data.slug, description: data.description,
    instructions: data.instructions, welcome: data.welcome, suggested_prompts: data.suggested_prompts, space_ids: data.space_ids, access: data.access });

  const save = async () => {
    setSaving(true);
    try { await api.patch(`/api/admin/knowledge/agents/${id}`, draft); toast("Gemt"); await reload(); }
    catch (e) { toast(errorText(e)); }
    finally { setSaving(false); }
  };

  return (
    <>
      <Link to="/knowledge" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, color: "#1d4ed8", textDecoration: "none", fontWeight: 500 }}>
        <ArrowLeft size={14} /> Viden
      </Link>
      <PageTitle title={data.name} aside={
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {data.status === "published" && <a href={`/extension/agent/${data.slug}`} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
            <Btn icon={ExternalLink}>Åbn agenten</Btn></a>}
          <Btn icon={Save} disabled={!dirty || saving} onClick={save}>{saving ? "Gemmer…" : "Gem"}</Btn>
          <Btn kind="primary" icon={Rocket} disabled={dirty} title={dirty ? "Gem først" : undefined} onClick={() => setPublishing(true)}>Udgiv</Btn>
        </div>
      }>
        <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
          {data.status === "published" ? <Pill tone="green">Udgivet {version(data)}</Pill> : <Pill tone="gray">Kladde</Pill>}
          {data.status === "published" && data.unpublished_changes && <Pill tone="orange">Ændringer, der ikke er udgivet</Pill>}
          {dirty && <Pill tone="blue">Ikke gemt</Pill>}
        </span>
      </PageTitle>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 20, alignItems: "flex-start" }}>
        <div style={{ flex: "999 1 520px", minWidth: 0, display: "flex", flexDirection: "column", gap: 14 }}>
          <section style={{ ...card, padding: 18, display: "flex", flexDirection: "column", gap: 12 }}>
            <span style={sectionLabel}>Identitet</span>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
              <label style={label}>Navn<input value={draft.name} onChange={(e) => set("name", e.target.value)} style={input} /></label>
              <label style={label}>Rute
                <span style={{ display: "flex", border: "1px solid #e2e8f0", borderRadius: 8, overflow: "hidden" }}>
                  <span style={{ padding: "0 8px", display: "flex", alignItems: "center", background: "#f1f5f9", color: "#475569", fontFamily: MONO, fontSize: 12 }}>/extension/agent/</span>
                  <input value={draft.slug} onChange={(e) => set("slug", e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-"))}
                    style={{ ...input, border: 0, flex: 1, minWidth: 0, fontFamily: MONO, fontSize: 12 }} />
                </span>
              </label>
            </div>
            <label style={label}>Beskrivelse<input value={draft.description} onChange={(e) => set("description", e.target.value)} style={input}
              placeholder="Hvad agenten hjælper med" /></label>
            <label style={label}>Velkomst<input value={draft.welcome} onChange={(e) => set("welcome", e.target.value)} style={input}
              placeholder="Hej! Spørg mig om …" /></label>
          </section>

          <section style={{ ...card, padding: 18, display: "flex", flexDirection: "column", gap: 8 }}>
            <label style={label}><span style={sectionLabel}>Instruktioner</span>
              <textarea rows={6} value={draft.instructions} onChange={(e) => set("instructions", e.target.value)} style={textarea}
                placeholder="Du er personale-assistenten. Svar kort og i et venligt sprog, og nævn altid hvilken politik du bruger." /></label>
            <span style={{ fontSize: 12, color: "#475569" }}>Instruktionerne styrer tone, rolle og sprog. Reglen om kun at svare ud fra de citerede uddrag tilføjes altid og kan ikke fjernes.</span>
          </section>

          <section style={{ ...card, padding: 18, display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={sectionLabel}>Viden — hvilke vidensrum må agenten bruge?</span>
            {(spaces.data ?? []).length === 0 && <span style={{ fontSize: 13, color: "#475569" }}>Ingen vidensrum endnu.</span>}
            {(spaces.data ?? []).map((s) => {
              const on = draft.space_ids.includes(s.id);
              return (
                <label key={s.id} style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 44, padding: "0 12px", borderRadius: 8,
                  border: `1px solid ${on ? "#bfdbfe" : "#e2e8f0"}`, background: on ? "#eff6ff" : "#ffffff", fontSize: 14 }}>
                  <input type="checkbox" checked={on} onChange={() => set("space_ids", on ? draft.space_ids.filter((x) => x !== s.id) : [...draft.space_ids, s.id])} />
                  <span style={{ flex: 1, fontWeight: on ? 600 : 400 }}>{s.name}</span>
                  <span style={{ fontSize: 12, color: "#475569" }}>{s.indexed} af {s.documents} indekseret</span>
                </label>
              );
            })}
          </section>

          <section style={{ ...card, padding: 18, display: "flex", flexDirection: "column", gap: 10 }}>
            <span style={sectionLabel}>Startspørgsmål</span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {draft.suggested_prompts.map((p, i) => (
                <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "4px 4px 4px 12px", borderRadius: 9999,
                  border: "1px solid #e2e8f0", fontSize: 13 }}>{p}
                  <IconBtn icon={X} label={`Fjern ${p}`} size={24} iconSize={12} onClick={() => set("suggested_prompts", draft.suggested_prompts.filter((_, j) => j !== i))} />
                </span>
              ))}
            </div>
            <form onSubmit={(e) => { e.preventDefault(); if (newPrompt.trim()) { set("suggested_prompts", [...draft.suggested_prompts, newPrompt.trim()]); setNewPrompt(""); } }}
              style={{ display: "flex", gap: 8 }}>
              <label style={{ flex: 1, display: "flex" }}><span style={srOnly}>Nyt startspørgsmål</span>
                <input value={newPrompt} onChange={(e) => setNewPrompt(e.target.value)} placeholder="Tilføj et startspørgsmål…" style={{ ...input, flex: 1 }} /></label>
              <Btn type="submit" icon={Plus} disabled={!newPrompt.trim() || draft.suggested_prompts.length >= 12}>Tilføj</Btn>
            </form>
          </section>

          <section style={{ ...card, padding: 18, display: "flex", flexDirection: "column", gap: 10 }}>
            <span style={sectionLabel}>Adgang</span>
            {(["all", "members"] as const).map((a) => (
              <label key={a} style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 36, fontSize: 14 }}>
                <input type="radio" name="access" checked={draft.access === a} onChange={() => set("access", a)} />
                {a === "all" ? "Alle, der er logget ind" : "Kun medlemmer (og admins)"}
              </label>
            ))}
            {data.access === "members" && <Members agent={data} onChange={reload} />}
          </section>
        </div>

        <aside style={{ flex: "1 1 340px", minWidth: 0, display: "flex", flexDirection: "column", gap: 14 }}>
          <Preview agentId={data.id} dirty={dirty} />
          <section style={{ ...card, padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
            <span style={{ ...sectionLabel, display: "flex", alignItems: "center", gap: 6 }}><History size={13} /> Versioner</span>
            {data.versions.length === 0 && <span style={{ fontSize: 13, color: "#475569" }}>Ikke udgivet endnu. Brugerne chatter altid med den seneste udgivne version.</span>}
            {data.versions.map((v, i) => (
              <div key={v.id} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, padding: 10, borderRadius: 8,
                border: `1px solid ${i === 0 ? "#bfdbfe" : "#e2e8f0"}`, background: i === 0 ? "#eff6ff" : "#ffffff" }}>
                <b>v{v.major}.{v.minor}</b>{i === 0 && <Pill tone="blue">Aktuel</Pill>}
                <span style={{ flex: "1 1 140px", fontSize: 12, color: "#334155" }}>{v.note || "—"} · {v.created_by ?? "ukendt"} · {fmtDate(v.created_at)}</span>
                {i > 0 && <Btn height={32} onClick={async () => {
                  if (!confirm(`Gendan v${v.major}.${v.minor}? Den kopieres til arbejdskopien og udgives som en ny minor-version.`)) return;
                  try { const r = await api.post<{ version: string }>(`/api/admin/knowledge/agents/${data.id}/versions/${v.id}/promote`); toast(`Udgivet som v${r.version}`); reload(); }
                  catch (e) { toast(errorText(e)); }
                }}>Gendan</Btn>}
              </div>
            ))}
          </section>
          <Btn icon={Trash2} style={{ color: "#b91c1c", borderColor: "#fecaca", alignSelf: "flex-start" }} onClick={async () => {
            if (!confirm(`Slet „${data.name}“ og alle samtaler med den? Det kan ikke fortrydes.`)) return;
            try { await api.del(`/api/admin/knowledge/agents/${data.id}`); navigate("/knowledge"); } catch (e) { toast(errorText(e)); }
          }}>Slet agent</Btn>
        </aside>
      </div>
      {publishing && <PublishDialog agent={data} onClose={() => setPublishing(false)} onDone={(v) => { setPublishing(false); toast(`Udgivet som v${v}`); reload(); }} />}
    </>
  );
}

function Members({ agent, onChange }: { agent: AgentData; onChange: () => void }) {
  const [email, setEmail] = useState("");
  const [privilege, setPrivilege] = useState<"use" | "manage">("use");
  const [error, setError] = useState<string | null>(null);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingTop: 6, borderTop: "1px solid #f1f5f9" }}>
      <form style={{ display: "flex", flexWrap: "wrap", gap: 8 }} onSubmit={async (e) => {
        e.preventDefault();
        try { await api.put(`/api/admin/knowledge/agents/${agent.id}/members`, { email, privilege }); setEmail(""); setError(null); onChange(); }
        catch (err) { setError(errorText(err)); }
      }}>
        <label style={{ flex: "1 1 220px", display: "flex" }}><span style={srOnly}>E-mail</span>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="navn@firma.dk" style={{ ...input, flex: 1 }} /></label>
        <label style={{ display: "flex" }}><span style={srOnly}>Rettighed</span>
          <select value={privilege} onChange={(e) => setPrivilege(e.target.value as "use" | "manage")} style={select}>
            <option value="use">Bruge</option><option value="manage">Administrere</option></select></label>
        <Btn type="submit" kind="primary" icon={Plus}>Tilføj</Btn>
      </form>
      {error && <ErrorBox>{error}</ErrorBox>}
      {agent.members.map((m) => (
        <div key={m.user_id} style={{ display: "flex", alignItems: "center", gap: 10, minHeight: 40, fontSize: 14 }}>
          <span style={{ flex: 1, display: "flex", flexDirection: "column" }}><b style={{ fontWeight: 600 }}>{m.display_name || m.email}</b>
            <span style={{ fontSize: 12, color: "#475569" }}>{m.email}</span></span>
          <span style={{ fontSize: 12, color: "#475569" }}>{m.privilege === "manage" ? "Administrere" : "Bruge"}</span>
          <IconBtn icon={X} label={`Fjern ${m.display_name || m.email}`} onClick={async () => {
            await api.del(`/api/admin/knowledge/agents/${agent.id}/members/${m.user_id}`); onChange();
          }} />
        </div>
      ))}
    </div>
  );
}

function Preview({ agentId, dirty }: { agentId: number; dirty: boolean }) {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<{ q: string; a: Answer | null; error?: string }[]>([]);
  const ask = async () => {
    const q = question.trim();
    if (!q) return;
    setQuestion("");
    setTurns((t) => [...t, { q, a: null }]);
    try {
      const a = await api.post<Answer>(`/api/admin/knowledge/agents/${agentId}/preview`, { question: q });
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { q, a } : x)));
    } catch (e) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { q, a: null, error: errorText(e) } : x)));
    }
  };
  return (
    <section style={{ ...card, display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 16px", borderBottom: "1px solid #e2e8f0" }}>
        <Play size={15} color="#2563eb" /><b style={{ flex: 1 }}>Prøv agenten</b>
        <span style={{ fontSize: 12, color: "#475569" }}>{dirty ? "gemt arbejdskopi" : "arbejdskopi"}</span>
      </div>
      <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10, minHeight: 220, maxHeight: 480, overflow: "auto" }}>
        {turns.length === 0 && <span style={{ fontSize: 13, color: "#475569" }}>Stil et spørgsmål for at se agentens svar og kilder. Svarene gemmes ikke.</span>}
        {turns.map((t, i) => (
          <div key={i} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <span style={{ alignSelf: "flex-end", maxWidth: "85%", padding: "8px 12px", borderRadius: "12px 12px 4px 12px", background: "#2563eb", color: "#fff", fontSize: 13 }}>{t.q}</span>
            <div style={{ maxWidth: "95%", padding: "10px 12px", borderRadius: "12px 12px 12px 4px", background: "#f1f5f9", fontSize: 13, whiteSpace: "pre-wrap" }}>
              {t.error ? <span style={{ color: "#b91c1c" }}>{t.error}</span> : t.a ? t.a.answer : "Tænker…"}
              {t.a && t.a.citations.length > 0 && (
                <span style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                  {t.a.citations.map((c) => <span key={c.n} style={{ fontSize: 11, padding: "2px 8px", borderRadius: 6, background: "#fff", border: "1px solid #cbd5e1" }}>
                    [{c.n}] {c.filename}{c.section ? ` · ${c.section}` : ""}{c.page ? ` · s. ${c.page}` : ""}</span>)}
                </span>
              )}
              {t.a && <span style={{ display: "block", marginTop: 6, fontSize: 11, color: "#64748b" }}>Søgning: {t.a.method === "vector" ? "vektorer" : "fuldtekst"}</span>}
            </div>
          </div>
        ))}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); ask(); }} style={{ display: "flex", gap: 8, padding: 12, borderTop: "1px solid #e2e8f0" }}>
        <label style={{ flex: 1, display: "flex" }}><span style={srOnly}>Spørgsmål</span>
          <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Stil et spørgsmål…" style={{ ...input, flex: 1, height: 40 }} /></label>
        <Btn type="submit" kind="primary" icon={Send} height={40} disabled={!question.trim()}>Spørg</Btn>
      </form>
    </section>
  );
}

function PublishDialog({ agent, onClose, onDone }: { agent: AgentData; onClose: () => void; onDone: (v: string) => void }) {
  const first = agent.version_major === 0;
  const [kind, setKind] = useState<"minor" | "major">(first ? "major" : "minor");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const next = (k: "minor" | "major") => first ? "1.0" : k === "major" ? `${agent.version_major + 1}.0` : `${agent.version_major}.${agent.version_minor + 1}`;
  return (
    <Modal onClose={onClose} width={460}>
      <h2 style={{ margin: 0, fontSize: 18 }}>Udgiv {agent.name}</h2>
      <p style={{ margin: 0, fontSize: 13, color: "#475569" }}>Udgivelsen fryser et uforanderligt snapshot. Brugerne på <span style={{ fontFamily: MONO }}>/extension/agent/{agent.slug}</span> får det med det samme.</p>
      {!first && (["minor", "major"] as const).map((k) => (
        <label key={k} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: 12, borderRadius: 10, border: `1px solid ${kind === k ? "#bfdbfe" : "#e2e8f0"}`,
          background: kind === k ? "#eff6ff" : "#fff" }}>
          <input type="radio" checked={kind === k} onChange={() => setKind(k)} style={{ marginTop: 3 }} />
          <span style={{ display: "flex", flexDirection: "column" }}><b>{k === "minor" ? "Mindre" : "Større"} — v{next(k)}</b>
            <span style={{ fontSize: 12, color: "#475569" }}>{k === "minor" ? "Små forbedringer og rettelser." : "Væsentlige ændringer i adfærd eller viden."}</span></span>
        </label>
      ))}
      <label style={label}>Note<input value={note} onChange={(e) => setNote(e.target.value)} style={input} placeholder="Hvad er ændret?" /></label>
      {error && <ErrorBox>{error}</ErrorBox>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn onClick={onClose}>Annullér</Btn>
        <Btn kind="primary" icon={Rocket} onClick={async () => {
          try { const r = await api.post<{ version: string }>(`/api/admin/knowledge/agents/${agent.id}/publish`, { kind, note }); onDone(r.version); }
          catch (e) { setError(errorText(e)); }
        }}>Udgiv v{next(kind)}</Btn>
      </div>
    </Modal>
  );
}
