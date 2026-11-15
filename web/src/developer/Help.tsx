// /developer/hjaelp — what Gær Developer is, keyboard shortcuts and where things live. Plus the placeholder for
// studio pages that a later phase fills.
import { Link } from "react-router-dom";
import { DevPage } from "./DeveloperShell";
import { HELP, NAV } from "./nav";

export default function Help() {
  return (
    <DevPage title="Om Gær Developer" intro="Fire studier, der definerer artefakter i én fælles kerne. Udførelsen sker i en fælles runtime, så MCP og API altid svarer ens.">
      <div className="dev-cards">
        <div className="dev-card">
          <strong>Tastatur</strong>
          <span className="small"><kbd>⌘K</kbd> / <kbd>Ctrl+K</kbd> kommandopalet · <kbd>⌘B</kbd> / <kbd>Ctrl+B</kbd> smal eller bred sidebar</span>
          <span className="small">Håndtaget på sidebarens højre kant flyttes med piletasterne (Shift for større skridt), Enter skifter smal/bred.</span>
        </div>
        <div className="dev-card">
          <strong>Versioner og godkendelse</strong>
          <span className="small">Patch udgives straks. Minor kræver Responsible og Consulted, major Accountable, Responsible og Consulted. Flowet slås til pr. artefakt.</span>
        </div>
        <div className="dev-card">
          <strong>Maskering</strong>
          <span className="small">Hvid og grøn vises, gul afrundes eller vises som interval, rød maskeres. Persondata maskeres mindst som rød. Det gælder også cache, log og forhåndsvisning.</span>
        </div>
      </div>
      <div className="dev-cards">
        {[...NAV.flatMap((g) => g.items), HELP].map((i) => (
          <div key={i.key} className="dev-card">
            <Link to={i.path}><strong>{i.label}</strong></Link>
            {i.local.map((l) => <Link key={l.path} to={l.path} className="small">{l.label}</Link>)}
          </div>
        ))}
      </div>
    </DevPage>
  );
}

export function Soon({ title, text }: { title: string; text: string }) {
  return (
    <DevPage title={title} intro={text}>
      <div className="dev-card"><span className="muted small">Denne del af Gær Developer er ikke bygget endnu. Se specs/developer/README.md for status.</span></div>
    </DevPage>
  );
}
