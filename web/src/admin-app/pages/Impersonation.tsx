// /admin/impersonation → Efterligning (screen 04), after happy-groups ImpersonationPage.tsx without groups.
import { useState } from "react";
import { Plus, Sparkles, Trash2, VenetianMask } from "lucide-react";
import { api, errorText } from "../../api";
import { useLoad } from "../../ui";
import { useAdmin } from "../context";
import { card, initials, input, label, MONO, select, SANS } from "../theme";
import { Btn, ErrorBox, IconBtn, PageTitle, useToast } from "../ui";

type TestUser = { id: number; display_name: string; role: "user" | "admin"; handle: string };

export default function Impersonation() {
  const { impersonating, refreshImpersonation } = useAdmin();
  const say = useToast();
  const { data, error, reload } = useLoad(() => api.get<{ users: TestUser[] }>("/api/impersonation/users"), []);
  const [name, setName] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const tests = data?.users ?? [];

  const act = async (call: () => Promise<unknown>, after?: () => void) => {
    try {
      await call();
      after?.();
      await Promise.all([reload(), refreshImpersonation()]);
    } catch (e) {
      say(errorText(e));
    }
  };

  const add = () => act(() => api.post("/api/impersonation/users", { display_name: name.trim(), role }), () => {
    say(`${name.trim()} er oprettet`);
    setName("");
  });
  const presets = () => act(async () => {
    const r = await api.post<{ created: TestUser[] }>("/api/impersonation/users/presets");
    say(r.created.length ? `${r.created.map((u) => u.display_name).join(" og ")} er oprettet` : "Du har allerede én testkonto pr. rolle");
  });
  const switchTo = (t: TestUser) => act(() => api.post("/api/impersonation/start", { user_id: t.id }),
    () => say(`Efterligner ${t.display_name}. Appen på / viser nu testkontoen.`));
  const remove = (t: TestUser) => act(() => api.del(`/api/impersonation/users/${t.id}`), () => say(`${t.display_name} er slettet`));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 880 }}>
      <PageTitle title="Efterligning" icon={<VenetianMask size={24} color="#f59e0b" strokeWidth={2} />}>
        Opret testkonti og se appen på / som dem. En testkonto kan ikke logge ind selv, og kun du kan se og bruge dine egne. Start, stop,
        oprettelse og sletning skrives i audit-loggen.
      </PageTitle>
      {error && <ErrorBox>{error}</ErrorBox>}
      <div style={{ ...card, padding: 20, display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: 15, fontWeight: 600 }}>Opret testkonto</span>
          <span style={{ fontSize: 13, color: "#64748b" }}>„Én pr. rolle“ opretter Test Bruger og Test Admin, hvis de mangler.</span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 14 }}>
          <label style={label}>Visningsnavn
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="Test Kasserer" style={input} />
          </label>
          <label style={label}>Rolle
            <select value={role} onChange={(e) => setRole(e.target.value as "user" | "admin")} style={select}>
              <option value="user">Bruger</option>
              <option value="admin">Admin</option>
            </select>
          </label>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Btn kind="primary" icon={Plus} onClick={add} disabled={!name.trim()}>Tilføj</Btn>
          <Btn icon={Sparkles} onClick={presets}>Én pr. rolle</Btn>
        </div>
      </div>
      <div style={{ ...card, padding: 20, display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={{ fontSize: 15, fontWeight: 600, paddingBottom: 6 }}>Dine testkonti</span>
        {data && tests.length === 0 && <span style={{ fontSize: 13, color: "#64748b" }}>Ingen testkonti endnu.</span>}
        {tests.map((t) => {
          const current = impersonating?.id === t.id;
          return (
            <div key={t.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "10px 0", borderTop: "1px solid #f1f5f9" }}>
              <span style={{ width: 30, height: 30, flex: "none", borderRadius: 9999, background: "#fef3c7", color: "#92400e", display: "flex",
                alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 700 }}>{initials(t.display_name)}</span>
              <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
                <span style={{ fontWeight: 500 }}>{t.display_name}</span>
                <span style={{ fontSize: 12, color: "#64748b", fontFamily: MONO }}>{t.handle}</span>
              </div>
              <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 9999, border: "1px solid #e2e8f0", color: "#475569" }}>
                {t.role === "admin" ? "Admin" : "Bruger"}
              </span>
              {current
                ? <span style={{ fontSize: 11, fontWeight: 700, padding: "3px 10px", borderRadius: 9999, background: "#0f172a", color: "#ffffff" }}>Aktiv</span>
                : (
                  <button type="button" onClick={() => switchTo(t)} style={{ display: "inline-flex", alignItems: "center", gap: 6, height: 32, padding: "0 12px",
                    borderRadius: 8, border: 0, cursor: "pointer", background: "#2563eb", color: "#f8fafc", font: `500 13px ${SANS}` }}>
                    <VenetianMask size={14} strokeWidth={2} />Skift til
                  </button>
                )}
              <IconBtn icon={Trash2} label="Slet testkonto" onClick={() => remove(t)} style={current ? { opacity: 0.35, pointerEvents: "none" } : undefined} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
