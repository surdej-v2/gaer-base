// /extension/agent and /extension/agent/:slug — Viden for users (specs/knowledge): the agents they may use, and a chat
// with one agent. Runs in the browser extension's side panel (embeddable, own chrome) or in a normal tab; the layout
// stacks the conversation list above the chat when the panel is narrow.

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errorText } from "../api";
import { useLoad } from "../ui";

type Profile = { slug: string; name: string; description: string; welcome: string; suggested_prompts: string[]; version: string; can_manage: boolean };
type Citation = { n: number; document_id: number; filename: string; section: string | null; page: number | null };
type Message = { id: number; role: "user" | "agent"; text: string; citations: Citation[]; created_at: string; pending?: boolean; error?: boolean };
type Conversation = { id: number; title: string; updated_at: string; messages: number };

const srOnly: CSSProperties = { position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap" };

const initials = (name: string) => name.split(/\s+/).filter(Boolean).map((w) => w[0]).slice(0, 2).join("").toUpperCase() || "A";

function Mark({ name, size = 36 }: { name: string; size?: number }) {
  return (
    <span aria-hidden="true" style={{ width: size, height: size, flex: "none", borderRadius: size / 4, background: "var(--hm-primary)", color: "#fff",
      display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: size * 0.36, fontWeight: 700 }}>{initials(name)}</span>
  );
}

export function AgentList() {
  const { data, error } = useLoad(() => api.get<Profile[]>("/api/knowledge/agents"), []);
  return (
    <div className="embed-shell">
      <header className="embed-header"><strong>Agenter</strong></header>
      <main className="embed-main">
        {error && <div className="alert error">{error}</div>}
        {data && data.length === 0 && <p className="muted">Du har ikke adgang til nogen agenter endnu.</p>}
        {data?.map((a) => (
          <Link key={a.slug} to={`/extension/agent/${a.slug}`} className="card" style={{ flexDirection: "row", alignItems: "center", gap: 12, textDecoration: "none" }}>
            <Mark name={a.name} />
            <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
              <strong>{a.name}</strong>
              {a.description && <span className="small muted">{a.description}</span>}
            </span>
          </Link>
        ))}
      </main>
    </div>
  );
}

export function AgentChat() {
  const { slug = "" } = useParams();
  const profile = useLoad(() => api.get<Profile>(`/api/knowledge/agents/${slug}`), [slug]);
  const conversations = useLoad(() => api.get<Conversation[]>(`/api/knowledge/agents/${slug}/conversations`), [slug]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { end.current?.scrollIntoView?.({ block: "end" }); }, [messages]);

  const open = async (id: number) => {
    const c = await api.get<{ messages: Message[] }>(`/api/knowledge/conversations/${id}`);
    setConversationId(id);
    setMessages(c.messages);
    setShowHistory(false);
  };

  const newChat = () => { setConversationId(null); setMessages([]); setShowHistory(false); };

  const ask = async (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    setQuestion("");
    setBusy(true);
    setMessages((m) => [...m, { id: -Date.now(), role: "user", text: q, citations: [], created_at: "" },
      { id: -Date.now() - 1, role: "agent", text: "Tænker…", citations: [], created_at: "", pending: true }]);
    try {
      const r = await api.post<{ conversation_id: number; message: Message }>(`/api/knowledge/agents/${slug}/ask`,
        { question: q, conversation_id: conversationId });
      setConversationId(r.conversation_id);
      setMessages((m) => [...m.filter((x) => !x.pending), r.message]);
      conversations.reload();
    } catch (e) {
      setMessages((m) => [...m.filter((x) => !x.pending), { id: -Date.now(), role: "agent", text: errorText(e), citations: [], created_at: "", error: true }]);
    } finally {
      setBusy(false);
    }
  };

  const openSource = async (c: Citation) => {
    try {
      const r = await api.get<{ url: string }>(`/api/knowledge/agents/${slug}/documents/${c.document_id}/link`);
      window.open(r.url, "_blank", "noopener");
    } catch (e) {
      alert(errorText(e));
    }
  };

  if (profile.error) {
    return (
      <div className="embed-shell"><main className="embed-main">
        <div className="alert error">{profile.error}</div>
        <Link to="/extension/agent">← Alle agenter</Link>
      </main></div>
    );
  }
  const a = profile.data;
  if (!a) return null;

  return (
    <div className="embed-shell" style={{ background: "#fff" }}>
      <header className="embed-header" style={{ gap: 10 }}>
        <Mark name={a.name} />
        <span style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}>
          <strong style={{ lineHeight: "20px" }}>{a.name}</strong>
          <span className="tiny muted">AI-agent · v{a.version}</span>
        </span>
        <button type="button" className="btn btn-ghost btn-sm" aria-expanded={showHistory} onClick={() => setShowHistory(!showHistory)}>Samtaler</button>
        <button type="button" className="btn btn-outline btn-sm" onClick={newChat}>Ny samtale</button>
      </header>

      <div style={{ flex: 1, display: "flex", flexWrap: "wrap", alignContent: "flex-start", minHeight: 0 }}>
        {showHistory && (
          <nav aria-label="Samtaler" style={{ flex: "1 1 240px", maxWidth: "100%", background: "var(--hm-muted)", borderRight: "1px solid var(--hm-border)",
            padding: 12, display: "flex", flexDirection: "column", gap: 4 }}>
            {(conversations.data ?? []).length === 0 && <span className="small muted">Ingen samtaler endnu — stil dit første spørgsmål.</span>}
            {(conversations.data ?? []).map((c) => (
              <div key={c.id} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <button type="button" onClick={() => open(c.id)} className="btn btn-ghost btn-sm"
                  style={{ flex: 1, minWidth: 0, justifyContent: "flex-start", fontWeight: c.id === conversationId ? 700 : 500 }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{c.title || "Samtale"}</span>
                </button>
                <button type="button" className="btn btn-ghost btn-sm" aria-label={`Slet samtalen ${c.title}`} onClick={async () => {
                  if (!confirm("Slet samtalen? Det kan ikke fortrydes.")) return;
                  await api.del(`/api/knowledge/conversations/${c.id}`);
                  if (c.id === conversationId) newChat();
                  conversations.reload();
                }}>×</button>
              </div>
            ))}
          </nav>
        )}

        <section aria-label="Chat" style={{ flex: "999 1 320px", minWidth: 0, display: "flex", flexDirection: "column" }}>
          <div style={{ flex: 1, padding: 16, display: "flex", flexDirection: "column", gap: 14, maxWidth: 820, width: "100%", boxSizing: "border-box", margin: "0 auto" }}>
            <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
              <Mark name={a.name} size={28} />
              <p style={{ margin: 0, padding: "10px 14px", borderRadius: "4px 12px 12px 12px", background: "var(--hm-muted)" }}>
                {a.welcome || `Spørg ${a.name} om hvad som helst — svarene kommer fra agentens vidensrum, med kilder.`}
              </p>
            </div>
            {messages.map((m) => m.role === "user" ? (
              <p key={m.id} style={{ margin: 0, alignSelf: "flex-end", maxWidth: "80%", padding: "10px 14px", borderRadius: "12px 12px 4px 12px",
                background: "var(--hm-primary)", color: "#fff", whiteSpace: "pre-wrap" }}>{m.text}</p>
            ) : (
              <div key={m.id} style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                <Mark name={a.name} size={28} />
                <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 8 }}>
                  <p role={m.pending ? "status" : undefined} style={{ margin: 0, padding: "10px 14px", borderRadius: "4px 12px 12px 12px", whiteSpace: "pre-wrap",
                    background: m.error ? "#fef2f2" : "var(--hm-muted)", color: m.error ? "#991b1b" : m.pending ? "var(--hm-muted-foreground)" : undefined }}>{m.text}</p>
                  {m.citations.length > 0 && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {m.citations.map((c) => (
                        <button key={c.n} type="button" className="btn btn-outline btn-sm" onClick={() => openSource(c)} style={{ fontWeight: 500, fontSize: 12 }}>
                          [{c.n}] {c.filename}{c.section ? ` · ${c.section}` : ""}{c.page ? ` · s. ${c.page}` : ""}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
            <div ref={end} />
          </div>

          <div style={{ padding: "8px 16px 16px", maxWidth: 820, width: "100%", boxSizing: "border-box", margin: "0 auto", display: "flex", flexDirection: "column", gap: 8 }}>
            {messages.length === 0 && a.suggested_prompts.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {a.suggested_prompts.map((p) => <button key={p} type="button" className="btn btn-outline btn-sm" style={{ fontWeight: 500 }} onClick={() => ask(p)}>{p}</button>)}
              </div>
            )}
            <form onSubmit={(e) => { e.preventDefault(); ask(question); }}
              style={{ display: "flex", gap: 8, alignItems: "flex-end", padding: 6, border: "1px solid #cbd5e1", borderRadius: 12 }}>
              <label style={{ flex: 1, display: "flex" }}><span style={srOnly}>Stil et spørgsmål</span>
                <textarea rows={1} value={question} placeholder="Stil et spørgsmål…" onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(question); } }}
                  style={{ flex: 1, minWidth: 0, minHeight: 40, padding: "9px 8px", border: 0, outline: "none", resize: "none", font: "inherit" }} /></label>
              <button type="submit" className="btn btn-primary" disabled={busy || !question.trim()}>Send</button>
            </form>
            <span className="tiny muted" style={{ textAlign: "center" }}>Svarene kommer kun fra agentens vidensrum. Tjek kilden, før du handler på et svar.</span>
          </div>
        </section>
      </div>
    </div>
  );
}
