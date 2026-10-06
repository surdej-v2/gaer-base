// /extension/chat — AI chat with the current web page as context (only on domains the user allowed).

import { useEffect, useState } from "react";
import { api } from "../api";
import { Chat } from "../ai";
import { domainOf, extension, isExtension, type PageInfo } from "../bridge/extension";

export default function ExtensionChat() {
  const [tab, setTab] = useState<PageInfo | null>(null);
  const [allowed, setAllowed] = useState(false);
  const [usePage, setUsePage] = useState(true);
  const domain = tab ? domainOf(tab.url) : "";

  useEffect(() => {
    isExtension().then(async (yes) => yes && setTab(await extension.pageInfo()));
    return extension.onTabChanged(setTab);
  }, []);

  useEffect(() => {
    setAllowed(false);
    if (domain && tab?.readable) {
      api.get<{ allowed: boolean }>(`/api/bridge-consent/check?domain=${encodeURIComponent(domain)}`).then((r) => setAllowed(r.allowed), () => undefined);
    }
  }, [domain, tab?.readable]);

  return (
    <div className="stack">
      {tab?.readable && (
        <label className="row small" style={{ fontWeight: 600 }}>
          <input type="checkbox" checked={usePage && allowed} disabled={!allowed} onChange={(e) => setUsePage(e.target.checked)} />
          {allowed ? `Brug siden (${domain}) som kontekst` : `Tillad ${domain} under Side for at bruge siden`}
        </label>
      )}
      <Chat
        surface="extension"
        contextHint="Spørg om siden, du ser på, eller om noget helt andet."
        getContext={async () => {
          if (!usePage || !allowed) return null;
          const s = await extension.snapshot(20000);
          return { label: `Websiden ${s.title} (${s.url})`, text: [s.description, s.selection && `Markeret: ${s.selection}`, s.text].filter(Boolean).join("\n\n") };
        }}
      />
    </div>
  );
}
