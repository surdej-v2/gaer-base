// Optional AI chat (see server/app/ai.py). The server is stateless: we keep the conversation here
// and send it each time. Tool calls from the model are run here (in Word/Outlook through the Office
// shell) and their results sent back, until the model answers with text.

import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, errorText } from "./api";
import { saveClip } from "./clips";
import { Alert, fmtNumber } from "./ui";

export type Surface = "app" | "extension" | "word" | "outlook" | "document" | "mobile";
type ToolCall = { id: string; name: string; arguments: string };
type Message = { role: "user" | "assistant" | "tool"; content: string; tool_calls?: ToolCall[]; tool_call_id?: string };
type Status = { enabled: boolean; used?: number; limit?: number };

export type ChatContext = { label: string; text: string } | null;
export type ToolRunner = (name: string, args: Record<string, unknown>) => Promise<string>;

const TOOL_LABEL: Record<string, string> = {
  get_selection: "Læser markeringen", insert_text: "Indsætter tekst", insert_paragraph: "Tilføjer afsnit",
  add_comment: "Tilføjer kommentar", set_subject: "Sætter emne", open_reply: "Åbner svar", save_clip: "Gemmer klip",
};
const MAX_TOOL_ROUNDS = 4;

export function useAiStatus() {
  const [status, setStatus] = useState<Status | null>(null);
  const reload = () => api.get<Status>("/api/ai/status").then(setStatus, () => setStatus({ enabled: false }));
  useEffect(() => { reload(); }, []);
  return { status, reload };
}

async function* streamChat(body: object, signal: AbortSignal): AsyncGenerator<[string, Record<string, unknown>]> {
  const headers: Record<string, string> = { "Content-Type": "application/json", "X-Gaer": "1" };
  if (window.self !== window.top) headers["X-Gaer-Embedded"] = "1";
  const res = await fetch("/api/ai/chat", { method: "POST", headers, body: JSON.stringify(body), signal, credentials: "same-origin" });
  if (!res.ok || !res.body) {
    let msg = `Fejl ${res.status}`;
    try { msg = (await res.json()).detail ?? msg; } catch { /* not JSON */ }
    throw new Error(msg);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const event = /^event: (.*)$/m.exec(block)?.[1];
      const data = /^data: (.*)$/m.exec(block)?.[1];
      if (event && data) yield [event, JSON.parse(data)];
    }
  }
}

/** A chat panel. `getContext` adds e.g. the web page or the document; `runTool` performs client tools. */
export function Chat({ surface, getContext, runTool, contextHint, placeholder = "Spørg om noget …" }: {
  surface: Surface;
  getContext?: () => Promise<ChatContext>;
  runTool?: ToolRunner;
  contextHint?: string;
  placeholder?: string;
}) {
  const { status, reload } = useAiStatus();
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [messages, activity]);

  if (!status) return null;
  if (!status.enabled) return <Alert kind="info">AI er ikke slået til.</Alert>;

  const execute = async (call: ToolCall): Promise<string> => {
    let args: Record<string, unknown> = {};
    try { args = JSON.parse(call.arguments || "{}"); } catch { return "Fejl: ugyldige argumenter"; }
    setActivity(TOOL_LABEL[call.name] ?? call.name);
    try {
      if (call.name === "save_clip") {
        await saveClip({ title: String(args.title ?? "AI-klip"), body: String(args.body ?? ""), source: surface === "word" || surface === "outlook" ? surface : surface === "mobile" ? "mobile" : surface === "extension" ? "extension" : "manual" });
        return "Gemt som klip";
      }
      if (!runTool) return "Værktøjet findes ikke her";
      return await runTool(call.name, args);
    } catch (e) {
      return `Fejl: ${errorText(e)}`;
    }
  };

  const send = async (e?: FormEvent) => {
    e?.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    setDraft("");
    setError(null);
    setBusy(true);
    const controller = new AbortController();
    abort.current = controller;
    let history: Message[] = [...messages, { role: "user", content: text }];
    setMessages(history);
    try {
      // Context is helpful, not required: if the page or document cannot be read, chat without it.
      let context: ChatContext = null;
      try {
        context = getContext ? await getContext() : null;
      } catch (err) {
        setError(`Konteksten kunne ikke læses (${errorText(err)}). Svaret er uden den.`);
      }
      for (let round = 0; round <= MAX_TOOL_ROUNDS; round++) {
        let answer = "";
        let calls: ToolCall[] = [];
        setActivity("Tænker …");
        setMessages([...history, { role: "assistant", content: "" }]);
        for await (const [event, data] of streamChat({ surface, messages: history, context: context?.text, context_label: context?.label }, controller.signal)) {
          if (event === "delta") {
            answer += String(data.text ?? "");
            setActivity(null);
            setMessages([...history, { role: "assistant", content: answer }]);
          } else if (event === "tool_calls") {
            calls = data.tool_calls as ToolCall[];
          } else if (event === "error") {
            throw new Error(String(data.message ?? "AI-fejl"));
          }
        }
        history = [...history, { role: "assistant", content: answer, ...(calls.length ? { tool_calls: calls } : {}) }];
        if (!calls.length) break;
        for (const call of calls) {
          history = [...history, { role: "tool", tool_call_id: call.id, content: await execute(call) }];
        }
        setMessages(history);
      }
      setMessages(history);
    } catch (err) {
      if ((err as Error).name !== "AbortError") setError(errorText(err));
      setMessages(history.filter((m) => m.role !== "assistant" || m.content || m.tool_calls));
    } finally {
      setBusy(false);
      setActivity(null);
      abort.current = null;
      reload();
    }
  };

  const visible = messages.filter((m) => m.role !== "tool" && (m.content || m.role === "user"));

  return (
    <div className="chat">
      <div className="chat-log">
        {visible.length === 0 && (
          <p className="muted small">{contextHint ?? "Stil et spørgsmål."} Samtalen gemmes ikke.</p>
        )}
        {visible.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>{m.content}</div>
        ))}
        {messages.flatMap((m) => m.tool_calls ?? []).length > 0 && (
          <div className="row" style={{ "--gap": "4px" } as React.CSSProperties}>
            {messages.flatMap((m) => m.tool_calls ?? []).map((c) => <span key={c.id} className="pill mono">{TOOL_LABEL[c.name] ?? c.name}</span>)}
          </div>
        )}
        {activity && <p className="muted tiny">{activity}</p>}
        <div ref={end} />
      </div>
      <Alert>{error}</Alert>
      <form className="chat-input" onSubmit={send}>
        <textarea value={draft} placeholder={placeholder} rows={2} onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        {busy ? <button type="button" className="btn btn-outline" onClick={() => abort.current?.abort()}>Stop</button>
          : <button className="btn btn-primary" disabled={!draft.trim()}>Send</button>}
      </form>
      <div className="spread tiny muted">
        <span>{messages.length > 0 && <button className="btn btn-ghost btn-sm" onClick={() => setMessages([])} disabled={busy}>Ny samtale</button>}</span>
        {status.limit ? <span>{fmtNumber(status.used)} af {fmtNumber(status.limit)} tokens brugt denne måned</span> : null}
      </div>
    </div>
  );
}
