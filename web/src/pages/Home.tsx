import { Link } from "react-router-dom";
import { useAuth } from "../auth";

export default function Home() {
  const { user, scope, config, loading } = useAuth();
  const name = config?.app_name ?? "Gær";

  if (loading) return null;
  if (!user || scope !== "full") {
    // Anonymous visitors belong on the public website (the server redirects / there as well).
    window.location.replace("/w/");
    return null;
  }

  return (
    <div className="page">
      <div className="stack" style={{ "--gap": "8px" } as React.CSSProperties}>
        <div className="eyebrow">{name}</div>
        <h1>Hej{user.display_name ? ` ${user.display_name}` : ""}</h1>
      </div>
      {user.passkeys === 0 && (
        <div className="alert warn">
          Du har ikke oprettet en passkey endnu. Du har {user.grace_left} {user.grace_left === 1 ? "login" : "logins"} med
          engangskode tilbage. <Link to="/konto">Opret passkey</Link>
        </div>
      )}
      <div className="grid">
        <Link to="/filer" className="card">
          <span className="eyebrow">Object Storage</span>
          <h3>Mine filer</h3>
          <p className="muted small">Upload, hent og slet dine filer.</p>
        </Link>
        <Link to="/konto" className="card">
          <span className="eyebrow">Konto</span>
          <h3>Passkeys og sessioner</h3>
          <p className="muted small">Navn, passkeys, aktive logins og dine data.</p>
        </Link>
        {user.role === "admin" && (
          <Link to="/admin" className="card">
            <span className="eyebrow mono">/admin</span>
            <h3>Administration</h3>
            <p className="muted small">Brugere, logs, analytics, heatmaps, GDPR og indstillinger.</p>
          </Link>
        )}
      </div>
    </div>
  );
}
