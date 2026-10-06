import { useEffect, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { browserSupportsWebAuthn } from "@simplewebauthn/browser";
import { api, errorText } from "../api";
import { loginWithPasskey, passkeyErrorText, registerPasskey, useAuth } from "../auth";
import { Alert } from "../ui";

type Step =
  | { kind: "phone" }
  | { kind: "code"; devCode?: string }
  | { kind: "offer"; graceLeft: number; admin: boolean }
  | { kind: "enroll"; reason: "grace_exhausted" | "new_device" };

type VerifyResult =
  | { status: "logged_in"; grace_left: number; offer_passkey: boolean; became_admin?: boolean }
  | { status: "passkey_required"; reason: "grace_exhausted" | "new_device" };

const GRACE_LOGINS = 3;

function GracePills({ used }: { used: number }) {
  return (
    <div className="grace-row" aria-label={`${used} af ${GRACE_LOGINS} logins med engangskode brugt`}>
      {Array.from({ length: GRACE_LOGINS }, (_, i) => (
        <span key={i} className={`pill ${i < used ? "blue" : ""}`}>
          {i + 1} · engangskode
        </span>
      ))}
      <span className="pill dark">Derefter passkey</span>
    </div>
  );
}

export default function Login({ home = "/", embedded = false }: { home?: string; embedded?: boolean }) {
  const { user, scope, refresh, config } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? home;
  const [step, setStep] = useState<Step>({ kind: "phone" });
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const supportsPasskey = browserSupportsWebAuthn();

  // Arriving with an enroll session (e.g. after a reload) goes straight to passkey creation.
  useEffect(() => {
    if (user && scope === "enroll" && step.kind === "phone") setStep({ kind: "enroll", reason: user.passkeys ? "new_device" : "grace_exhausted" });
    if (user && scope === "full" && step.kind === "phone") navigate(from, { replace: true });
  }, [user, scope, step.kind, navigate, from]);

  const run = async (fn: () => Promise<void>, passkey = false) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(passkey ? passkeyErrorText(e) : errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const sendCode = (e?: FormEvent) => {
    e?.preventDefault();
    run(async () => {
      const res = await api.post<{ dev_code?: string }>("/api/auth/otp/start", { phone });
      setCode("");
      setStep({ kind: "code", devCode: res.dev_code });
    });
  };

  const verify = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      const res = await api.post<VerifyResult>("/api/auth/otp/verify", { phone, code });
      await refresh();
      if (res.status === "logged_in") {
        if (supportsPasskey || res.became_admin) setStep({ kind: "offer", graceLeft: res.grace_left, admin: !!res.became_admin });
        else navigate(from, { replace: true });
      } else {
        setStep({ kind: "enroll", reason: res.reason });
      }
    });
  };

  const createPasskey = () =>
    run(async () => {
      await registerPasskey();
      await refresh();
      navigate(from, { replace: true });
    }, true);

  const passkeyLogin = () =>
    run(async () => {
      await loginWithPasskey();
      await refresh();
      navigate(from, { replace: true });
    }, true);

  return (
    <div className={`auth-shell${embedded ? " embedded" : ""}`}>
      <div className="auth-card glow">
        <div className="card">
          {step.kind === "phone" && (
            <form className="stack" style={{ "--gap": "16px" } as React.CSSProperties} onSubmit={sendCode}>
              <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
                <div className="eyebrow">Log ind</div>
                <h2>Dit telefonnummer</h2>
                <p className="muted small">Vi sender en engangskode på SMS.</p>
              </div>
              {config?.first_user && (
                <Alert kind="warn">
                  Instansen er ny og har ingen brugere. Du bliver den første bruger og dermed <strong>administrator</strong>.
                </Alert>
              )}
              <label className="field">
                Telefonnummer
                <input type="tel" autoComplete="tel" inputMode="tel" placeholder="12 34 56 78" value={phone}
                       onChange={(e) => setPhone(e.target.value)} required autoFocus />
              </label>
              <Alert>{error}</Alert>
              <button className="btn btn-primary btn-block" disabled={busy || phone.trim().length < 6}>
                Send kode
              </button>
              {supportsPasskey && (
                <>
                  <div className="row muted tiny" style={{ justifyContent: "center" }}>eller</div>
                  <button type="button" className="btn btn-dark btn-block" onClick={passkeyLogin} disabled={busy}>
                    Log ind med passkey
                  </button>
                </>
              )}
            </form>
          )}

          {step.kind === "code" && (
            <form className="stack" style={{ "--gap": "16px" } as React.CSSProperties} onSubmit={verify}>
              <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
                <div className="eyebrow">Engangskode</div>
                <h2>Skriv koden</h2>
                <p className="muted small">Vi har sendt en kode til {phone}. Den kan kun bruges én gang og udløber om få minutter.</p>
              </div>
              {step.devCode && <Alert kind="info">Udvikling: koden er <strong className="mono">{step.devCode}</strong></Alert>}
              <input className="code-input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code}
                     onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} aria-label="Engangskode" autoFocus />
              <Alert>{error}</Alert>
              <button className="btn btn-primary btn-block" disabled={busy || code.length !== 6}>Log ind</button>
              <div className="spread">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setStep({ kind: "phone" })}>Skift nummer</button>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => sendCode()} disabled={busy}>Send ny kode</button>
              </div>
            </form>
          )}

          {step.kind === "offer" && (
            <div className="stack" style={{ "--gap": "16px" } as React.CSSProperties}>
              <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
                <div className="eyebrow">Du er logget ind</div>
                <h2>Opret en passkey</h2>
                <p className="muted small">
                  Med en passkey logger du ind med fingeraftryk, ansigt eller enhedens kode. Du kan logge ind med
                  engangskode {GRACE_LOGINS} gange. Derefter skal du have en passkey.
                </p>
              </div>
              {step.admin && <Alert kind="success">Du er den første bruger og er nu administrator. Du finder administrationen under /admin.</Alert>}
              <GracePills used={GRACE_LOGINS - step.graceLeft} />
              <Alert>{error}</Alert>
              <button className="btn btn-primary btn-block" onClick={createPasskey} disabled={busy}>Opret passkey nu</button>
              <button className="btn btn-ghost btn-block" onClick={() => navigate(from, { replace: true })}>
                {step.graceLeft > 0 ? `Senere (${step.graceLeft} ${step.graceLeft === 1 ? "login" : "logins"} tilbage)` : "Senere"}
              </button>
            </div>
          )}

          {step.kind === "enroll" && (
            <div className="stack" style={{ "--gap": "16px" } as React.CSSProperties}>
              <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
                <div className="eyebrow">Passkey kræves</div>
                <h2>{step.reason === "new_device" ? "Ny enhed" : "Opret en passkey for at fortsætte"}</h2>
                <p className="muted small">
                  {step.reason === "new_device"
                    ? "Du har bekræftet dit nummer. Opret en passkey på denne enhed for at logge ind. Det tæller ikke som et login med engangskode."
                    : `Du har brugt dine ${GRACE_LOGINS} logins med engangskode. Opret en passkey for at komme videre.`}
                </p>
              </div>
              {step.reason === "grace_exhausted" && <GracePills used={GRACE_LOGINS} />}
              {!supportsPasskey && <Alert kind="warn">Din browser understøtter ikke passkeys. Prøv en nyere browser eller en anden enhed.</Alert>}
              <Alert>{error}</Alert>
              <button className="btn btn-primary btn-block" onClick={createPasskey} disabled={busy || !supportsPasskey}>Opret passkey</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
