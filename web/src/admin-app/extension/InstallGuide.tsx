// Side panel with installation guidance when there is no handshake with the Happy AI extension.
import { useState } from "react";
import { Download, RefreshCw } from "lucide-react";
import { useAuth } from "../../auth";
import { HAPPY_AI_STORE_URL } from "../../bridge/extension";
import logo from "../assets/happy-mates-logo.svg";
import { MONO, SANS } from "../theme";
import { Btn, CloseBtn, copyText, Drawer } from "../ui";
import { handshake, happyAiUi, useHappyAi } from "./happyAiStore";

const BROWSERS = { chrome: { label: "Chrome", url: "chrome://extensions" }, edge: { label: "Edge", url: "edge://extensions" } } as const;

export default function InstallGuide() {
  const { guideOpen, handshake: hs, attempt } = useHappyAi();
  const { config } = useAuth();
  const [browser, setBrowser] = useState<keyof typeof BROWSERS>("chrome");
  const [downloaded, setDownloaded] = useState(false);
  const [copied, setCopied] = useState(false);
  if (!guideOpen) return null;

  const origin = window.location.origin;
  const steps = [
    { title: "Åbn Happy AI i Chrome Web Store", download: true,
      body: browser === "chrome" ? "Udvidelsen ligger i Chrome Web Store."
        : "Edge kan installere fra Chrome Web Store. Første gang spørger Edge, om du vil tillade udvidelser fra andre butikker — vælg Tillad." },
    { title: browser === "chrome" ? "Klik „Føj til Chrome“" : "Klik „Hent“",
      body: "Godkend tilladelserne. Udvidelsen læser kun en side, når du beder om det, og spørger pr. domæne." },
    { title: "Fastgør udvidelsen", body: "Klik på puslespilsbrikken ved adresselinjen og sæt en nål ved Happy AI." },
    { title: "Peg på denne instans", body: "Højreklik på ikonet → Indstillinger, og sæt endpoint til instansens adresse.", code: origin },
    { title: "Åbn sidepanelet og log ind", body: "Log ind med dit telefonnummer i sidepanelet. Kom så tilbage og prøv handshake igen." },
  ];
  const HS = {
    checking: { title: "Leder efter udvidelsen …", detail: `PING · forsøg ${attempt} af 4`, bg: "#f8fafc", bd: "#e2e8f0", fg: "#334155", dot: "#2563eb", anim: "tgpulse 1s infinite" },
    none: { title: "Ingen handshake med udvidelsen", detail: "happyai-bridge · PING svarede ikke efter 4 forsøg", bg: "rgba(249,115,22,0.08)", bd: "rgba(249,115,22,0.3)",
      fg: "#9a3412", dot: "#f97316", anim: "none" },
    idle: { title: "Ikke tjekket endnu", detail: "happyai-bridge", bg: "#f8fafc", bd: "#e2e8f0", fg: "#334155", dot: "#94a3b8", anim: "none" },
  }[hs];
  const checking = hs === "checking";

  return (
    <Drawer width={440} z={46} onClose={happyAiUi.closeGuide} label="Installér udvidelsen">
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "16px 20px", borderBottom: "1px solid #e2e8f0" }}>
        <img src={logo} alt="" style={{ width: 26, height: 26 }} />
        <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
          <span className="tg-rainbow-text" style={{ fontSize: 13, fontWeight: 800, letterSpacing: "0.04em" }}>HAPPY AI</span>
          <span style={{ fontSize: 12, color: "#64748b" }}>Browserudvidelse til {config?.app_name ?? window.location.hostname}</span>
        </div>
        <CloseBtn onClick={happyAiUi.closeGuide} />
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: 20, display: "flex", flexDirection: "column", gap: 20 }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 10, padding: "12px 14px", borderRadius: 12, background: HS.bg, border: `1px solid ${HS.bd}`, fontSize: 13, color: HS.fg }}>
          <span style={{ width: 8, height: 8, flex: "none", marginTop: 5, borderRadius: 9999, background: HS.dot, animation: HS.anim }} />
          <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span style={{ fontWeight: 600 }}>{HS.title}</span>
            <span style={{ font: `11px ${MONO}`, opacity: 0.8 }}>{HS.detail}</span>
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <h2 style={{ margin: 0, fontSize: 20, lineHeight: 1.25, letterSpacing: "-0.015em", fontWeight: 700, textWrap: "balance" }}>Installér udvidelsen</h2>
          <p style={{ margin: 0, fontSize: 14, lineHeight: 1.55, color: "#64748b", textWrap: "pretty" }}>
            Med udvidelsen kan /admin læse den aktuelle fane, slå brugere op fra en markering og vise appen i sidepanelet — også som en testkonto.
          </p>
        </div>
        <div style={{ display: "flex", gap: 2, padding: 3, borderRadius: 9999, background: "#f1f5f9", alignSelf: "flex-start" }}>
          {(Object.keys(BROWSERS) as (keyof typeof BROWSERS)[]).map((k) => {
            const on = browser === k;
            return (
              <button key={k} type="button" onClick={() => setBrowser(k)} style={{ border: 0, cursor: "pointer", padding: "5px 14px", borderRadius: 9999,
                font: `600 12px ${SANS}`, background: on ? "#ffffff" : "transparent", color: on ? "#020817" : "#64748b", boxShadow: on ? "0 1px 2px rgba(0,0,0,0.08)" : "none" }}>
                {BROWSERS[k].label}
              </button>
            );
          })}
        </div>
        <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 14 }}>
          {steps.map((g, i) => (
            <li key={g.title} style={{ display: "flex", gap: 12 }}>
              <span style={{ width: 24, height: 24, flex: "none", borderRadius: 9999, background: i === 0 && downloaded ? "#16a34a" : "#f1f5f9",
                color: i === 0 && downloaded ? "#ffffff" : "#020817", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700 }}>{i + 1}</span>
              <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 6 }}>
                <span style={{ fontSize: 14, fontWeight: 600 }}>{g.title}</span>
                <span style={{ fontSize: 13, lineHeight: 1.5, color: "#64748b", textWrap: "pretty" }}>{g.body}</span>
                {g.code && (
                  <div style={{ display: "flex", alignItems: "center", gap: 8, height: 34, padding: "0 6px 0 10px", border: "1px solid #e2e8f0", borderRadius: 8, background: "#f8fafc" }}>
                    <span style={{ flex: 1, minWidth: 0, font: `13px ${MONO}`, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{g.code}</span>
                    <button type="button" onClick={() => { copyText(g.code!); setCopied(true); window.setTimeout(() => setCopied(false), 1500); }}
                      style={{ height: 26, padding: "0 10px", borderRadius: 6, border: "1px solid #e2e8f0", background: "#ffffff", cursor: "pointer", font: `500 12px ${SANS}`, color: "#020817" }}>
                      {copied ? "Kopieret" : "Kopiér"}
                    </button>
                  </div>
                )}
                {g.download && (
                  <Btn kind="primary" icon={Download} height={34} style={{ alignSelf: "flex-start" }}
                    onClick={() => { window.open(HAPPY_AI_STORE_URL, "_blank", "noopener"); setDownloaded(true); }}>
                    {downloaded ? "Åbn Chrome Web Store igen" : "Åbn Chrome Web Store"}
                  </Btn>
                )}
              </div>
            </li>
          ))}
        </ol>
        <p style={{ margin: 0, fontSize: 12, color: "#64748b", textWrap: "pretty" }}>
          Kan du ikke installere fra Chrome Web Store, fx på en låst arbejdscomputer, kan du hente en zip med instansens adresse under{" "}
          <a href="/admin/integrations">/admin/integrations</a> og indlæse den upakket i <span style={{ fontFamily: MONO }}>{BROWSERS[browser].url}</span>.
        </p>
      </div>
      <div style={{ padding: "12px 20px", borderTop: "1px solid #e2e8f0", display: "flex", alignItems: "center", gap: 10 }}>
        <Btn height={38} onClick={happyAiUi.closeGuide}>Senere</Btn>
        <Btn kind="primary" icon={RefreshCw} height={38} disabled={checking} style={{ marginLeft: "auto", gap: 8, padding: "0 18px", ...(checking ? { opacity: 0.6 } : {}) }}
          onClick={() => handshake(false)}>{checking ? "Leder …" : "Prøv handshake igen"}</Btn>
      </div>
    </Drawer>
  );
}
