import { api } from "../../api";
import { download, PageHeader, useLoad } from "../../ui";

type Manifest = { app: string; url: string; taskpane: string };
const NAMES: Record<string, string> = { word: "Word", excel: "Excel", powerpoint: "PowerPoint", outlook: "Outlook" };

export default function Integrations() {
  const manifests = useLoad(() => api.get<Manifest[]>("/api/office/manifests"));
  return (
    <>
      <PageHeader eyebrow="/admin/integrations" title="Browserudvidelse og Office">
        Appen kan åbnes i et sidepanel i Chrome og Edge og som tilføjelse i Word, Excel, PowerPoint og Outlook. Begge viser
        appen selv i en iframe, og brugeren logger ind som normalt.
      </PageHeader>

      <div className="grid" style={{ "--min": "320px" } as React.CSSProperties}>
        <div className="card">
          <span className="eyebrow mono">/extension</span>
          <h3>Browserudvidelse</h3>
          <p className="small muted">
            Sidepanelet viser /extension. Brugeren kan læse den aktuelle side og gemme den eller en markering som klip. Appen
            spørger om lov første gang på hvert domæne.
          </p>
          <ol className="small" style={{ margin: 0, paddingLeft: 18 }}>
            <li>Hent udvidelsen. Den peger allerede på denne instans.</li>
            <li>Pak zip-filen ud, og åbn <code>chrome://extensions</code> eller <code>edge://extensions</code>.</li>
            <li>Slå udviklertilstand til, og vælg “Indlæs upakket”.</li>
          </ol>
          <p className="tiny muted">Til mange brugere: udgiv mappen i Chrome Web Store eller Edge Add-ons, eller rul den ud via Intune/GPO.</p>
          <div><button className="btn btn-primary" onClick={() => download("/api/extension/download")}>Hent udvidelse (.zip)</button></div>
        </div>

        <div className="card">
          <span className="eyebrow mono">/office-addin</span>
          <h3>Office-tilføjelser</h3>
          <p className="small muted">
            Alle fire manifester peger på /office-addin, som finder ud af, om den kører i Word, Excel, PowerPoint eller Outlook,
            og viser det rigtige værktøj. Klip kan indsættes i dokumenter og mails, og markeringer og mails kan gemmes som klip.
          </p>
          <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
            {manifests.data?.map((m) => (
              <div key={m.app} className="spread small">
                <strong>{NAMES[m.app]}</strong>
                <button className="btn btn-outline btn-sm" onClick={() => download(m.url)}>Hent manifest</button>
              </div>
            ))}
          </div>
          <p className="tiny muted">
            Udrul via Microsoft 365 Admin Center → Integrerede apps → Upload brugerdefinerede apps. Til test: sideload manifestet
            i Office på nettet under Tilføjelsesprogrammer → Mine tilføjelsesprogrammer.
          </p>
        </div>
      </div>
    </>
  );
}
