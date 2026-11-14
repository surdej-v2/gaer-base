import { useEffect, useRef, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { browserSupportsWebAuthn } from "@simplewebauthn/browser";
import { api, errorText } from "../api";
import { loginWithLink, loginWithPasskey, passkeyErrorText, registerPasskey, useAuth, type LoginResult, type MfaMethod } from "../auth";
import { SecondFactor } from "../security";
import { Alert } from "../ui";
import { solveCaptcha } from "../captcha";
import { ProviderIcon, ssoErrorText, startUrl, type ProviderList } from "../loginProviders";

// After the code: users with "Kræv passkey" prove their second factor (step "mfa"); everybody else is logged in
// and offered a passkey (step "offer"). Admins also see how many grace logins /admin has left.
// A login link from the mail (/login#link=<token>) opens step "link": one click finishes the login, so a mail scanner
// that opens the link cannot use it up.
// Login providers (/admin/login-providers): their buttons replace the code. Back from one, the server sends the browser
// here with ?sso=mfa (second factor), ?sso=offer (passkey offer) or ?sso_error=<code>. A domain that requires its
// provider gets only that button.
type Step =
  | { kind: "phone" }
  | { kind: "link"; token: string }
  | { kind: "code"; devCode?: string; devLink?: string }
  | { kind: "offer"; adminGraceLeft: number | null; becameAdmin: boolean }
  | { kind: "mfa"; methods: MfaMethod[] };

const GRACE_LOGINS = 3;

type DemoAccount = { key: string; email: string; label: string; role: "admin" | "user"; description: string };

// Development only: the server offers one demo account per role (app/demo_logins.py). Each logs in through the
// ordinary one-time code, which the server returns to the page just as otp/start does with MAIL_PROVIDER=console.
function DemoLogins({ accounts, busy, onPick }: { accounts: DemoAccount[]; busy: boolean; onPick: (key: string) => void }) {
  return (
    <div className="stack" style={{ "--gap": "8px" } as React.CSSProperties} aria-label="Demo-konti">
      <div className="row muted tiny" style={{ justifyContent: "center" }}>Demo-konti · kun i udvikling</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 6 }}>
        {accounts.map((a) => (
          <button key={a.key} type="button" className="btn btn-outline btn-sm" disabled={busy} onClick={() => onPick(a.key)}
                  title={a.description} style={{ justifyContent: "flex-start", gap: 6 }}>
            <span className={`pill ${a.role === "admin" ? "blue" : ""}`} style={{ padding: "1px 7px", fontSize: 10 }}>
              {a.role === "admin" ? "admin" : "bruger"}
            </span>
            {a.label}
          </button>
        ))}
      </div>
      <p className="muted tiny" style={{ margin: 0 }}>
        Samme login som alle andre: engangskode, auth-log og sessioner. Efter tre logins kræver /admin en passkey — også for demo-konti.
      </p>
    </div>
  );
}

function GracePills({ left }: { left: number }) {
  const used = GRACE_LOGINS - left;
  return (
    <div className="grace-row" aria-label={`${used} af ${GRACE_LOGINS} logins til administrationen med engangskode brugt`}>
      {Array.from({ length: GRACE_LOGINS }, (_, i) => (
        <span key={i} className={`pill ${i < used ? "blue" : ""}`}>
          {i + 1} · engangskode
        </span>
      ))}
      <span className="pill dark">Derefter nøgle til /admin</span>
    </div>
  );
}

/** A path on this origin, or null: "//host" and "/\host" resolve elsewhere and must not be followed. */
function sameOriginPath(value: string | null): string | null {
  if (!value || !value.startsWith("/")) return null;
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin ? url.pathname + url.search + url.hash : null;
  } catch {
    return null;
  }
}

export default function Login({ home = "/", embedded = false }: { home?: string; embedded?: boolean }) {
  const { user, scope, refresh, config } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  // Where to go after login: state from RequireUser, or ?next= (same-site paths only) from /admin and MCP.
  const next = new URLSearchParams(location.search).get("next");
  const from = (location.state as { from?: string } | null)?.from ?? sameOriginPath(next) ?? home;
  // /admin is a separate app, so it needs a full page load.
  const goFrom = () => (from === "/admin" || from.startsWith("/admin/") ? window.location.assign(from) : navigate(from, { replace: true }));
  const [step, setStep] = useState<Step>(() => {
    const token = new URLSearchParams(window.location.hash.slice(1)).get("link");
    if (token) return { kind: "link", token };
    const q = new URLSearchParams(window.location.search);
    if (q.get("sso") === "offer") {
      const grace = q.get("grace");
      return { kind: "offer", adminGraceLeft: grace === null ? null : Number(grace), becameAdmin: false };
    }
    return { kind: "phone" };
  });
  // The token is not left in the address bar or the history.
  useEffect(() => {
    if (window.location.hash.startsWith("#link=")) {
      window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
    }
  }, []);
  const [channel, setChannel] = useState<"email" | "phone">("email");
  const [phone, setPhone] = useState("");
  const contact = channel === "email" ? { email: phone.trim() } : { phone };
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(() => ssoErrorText(new URLSearchParams(window.location.search).get("sso_error")));
  const supportsPasskey = browserSupportsWebAuthn();
  // Login providers: the buttons for no e-mail yet, then for the e-mail's domain (and whether it must use one).
  const [sso, setSso] = useState<ProviderList | null>(null);
  const [ssoHint, setSsoHint] = useState(false);
  const emailInput = useRef<HTMLInputElement | null>(null);
  const typedEmail = channel === "email" && /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(phone.trim()) ? phone.trim().toLowerCase() : "";
  useEffect(() => {
    if (embedded) return;
    const t = window.setTimeout(() => {
      api.get<ProviderList>(`/api/auth/providers${typedEmail ? `?email=${encodeURIComponent(typedEmail)}` : ""}`)
        .then(setSso).catch(() => setSso(null));
    }, typedEmail ? 350 : 0);
    return () => window.clearTimeout(t);
  }, [typedEmail, embedded]);
  const goProvider = (key: string) => window.location.assign(startUrl(key, from));
  const [demo, setDemo] = useState<DemoAccount[]>([]);
  useEffect(() => {
    if (!config?.demo_logins) return;
    api.get<{ accounts: DemoAccount[] }>("/api/auth/demo-logins").then((r) => setDemo(r.accounts)).catch(() => setDemo([]));
  }, [config?.demo_logins]);

  // Arriving with a pending login (e.g. after a reload) goes straight to the second factor.
  useEffect(() => {
    if (user && scope === "mfa" && step.kind === "phone") setStep({ kind: "mfa", methods: user.mfa.methods });
    if (user && scope === "full" && step.kind === "phone") goFrom();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, scope, step.kind, from]);

  const after = async (res: LoginResult) => {
    await refresh();
    if (res.status === "mfa_required") setStep({ kind: "mfa", methods: res.methods });
    else if ((res.offer_passkey && supportsPasskey) || res.became_admin || (res.admin_grace_left ?? null) !== null)
      setStep({ kind: "offer", adminGraceLeft: res.admin_grace_left ?? null, becameAdmin: !!res.became_admin });
    else goFrom();
  };

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

  // The robot check is solved in the background while the person types, so sending the code is not slower.
  const captcha = useRef<Promise<string | null> | null>(null);
  const nextCaptcha = () => {
    captcha.current = solveCaptcha();
    captcha.current.catch(() => {}); // the error is shown when the code is sent
  };
  useEffect(() => {
    if (config?.captcha && step.kind === "phone" && !captcha.current) nextCaptcha();
  }, [config?.captcha, step.kind]);

  const sendCode = (e?: FormEvent) => {
    e?.preventDefault();
    run(async () => {
      let solution: string | null = null;
      if (config?.captcha) {
        if (!captcha.current) nextCaptcha();
        try {
          solution = await captcha.current;
        } finally {
          captcha.current = null; // a solution works once
        }
      }
      const res = await api.post<{ dev_code?: string; dev_link?: string }>("/api/auth/otp/start", { ...contact, captcha: solution });
      setCode("");
      setStep({ kind: "code", devCode: res.dev_code, devLink: res.dev_link });
    });
  };

  const verify = (e: FormEvent) => {
    e.preventDefault();
    run(async () => {
      await after(await api.post<LoginResult>("/api/auth/otp/verify", { ...contact, code }));
    });
  };

  const createPasskey = () =>
    run(async () => {
      await registerPasskey();
      await refresh();
      goFrom();
    }, true);

  const passkeyLogin = () => run(async () => after(await loginWithPasskey()), true);

  const linkLogin = (token: string) => run(async () => after(await loginWithLink(token)));
  const signupOpen = config?.signup?.open ?? true;
  const signupContact = config?.signup?.contact ?? "";

  const demoLogin = (key: string) =>
    run(async () => {
      const start = await api.post<{ email: string; dev_code: string }>(`/api/auth/demo-logins/${key}`);
      setChannel("email");
      setPhone(start.email);
      await after(await api.post<LoginResult>("/api/auth/otp/verify", { email: start.email, code: start.dev_code }));
    });

  return (
    <div className={`auth-shell${embedded ? " embedded" : ""}`}>
      <div className="auth-card glow">
        <div className="card">
          {step.kind === "phone" && (
            <form className="stack" style={{ "--gap": "16px" } as React.CSSProperties}
                  onSubmit={(e) => { if (sso?.required) { e.preventDefault(); goProvider(sso.required.key); } else sendCode(e); }}>
              <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
                <div className="eyebrow">Log ind</div>
                <h2>{channel === "email" ? "Din e-mailadresse" : "Dit telefonnummer"}</h2>
                <p className="muted small">{channel === "email" ? "Vi sender en engangskode på mail." : "Vi sender en engangskode på SMS."}</p>
              </div>
              {config?.first_user && (
                <Alert kind="warn">
                  Instansen er ny og har ingen brugere. Du bliver den første bruger og dermed <strong>superadministrator</strong>.
                </Alert>
              )}
              {config?.demo_mode && (
                <Alert kind="info">Demo: koden bliver ikke sendt, men skrevet i serverens log.</Alert>
              )}
              {channel === "email" ? (
                <label className="field">
                  E-mail
                  <input ref={emailInput} type="email" autoComplete="email" placeholder="navn@firma.dk" value={phone}
                         onChange={(e) => setPhone(e.target.value)} required autoFocus />
                </label>
              ) : (
                <label className="field">
                  Telefonnummer
                  <input type="tel" autoComplete="tel" inputMode="tel" placeholder="12 34 56 78" value={phone}
                         onChange={(e) => setPhone(e.target.value)} required autoFocus />
                </label>
              )}
              <Alert>{error}</Alert>
              {sso?.required ? (
                <>
                  <div role="status" className="alert info" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                    <span>{sso.required.domain} logger ind med {sso.required.name}. Du sendes videre og kommer tilbage hertil.</span>
                  </div>
                  <button type="button" className="btn btn-dark btn-block" onClick={() => goProvider(sso.required!.key)} style={{ gap: 10 }}>
                    <ProviderIcon kind={sso.required.kind} />{sso.required.label}
                  </button>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setPhone(""); setError(null); emailInput.current?.focus(); }}>
                    Brug en anden e-mail
                  </button>
                </>
              ) : (
                <button className="btn btn-primary btn-block" disabled={busy || phone.trim().length < 6}>
                  {busy && config?.captcha ? "Tjekker og sender …" : "Send kode"}
                </button>
              )}
              {config?.captcha && (
                <p className="muted tiny" style={{ textAlign: "center" }}>
                  Et lille robottjek kører i din browser. Ingen cookies, intet sendes til andre.
                </p>
              )}
              {config?.sms_login && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setChannel(channel === "email" ? "phone" : "email"); setPhone(""); setError(null); }}>
                  {channel === "email" ? "Brug telefonnummer i stedet" : "Brug e-mail i stedet"}
                </button>
              )}
              {!sso?.required && (supportsPasskey || (sso && (sso.providers.length > 0 || sso.sso))) && (
                <div className="row muted tiny" style={{ justifyContent: "center" }}>eller</div>
              )}
              {!sso?.required && sso && (sso.providers.length > 0 || sso.sso) && (
                <div className="stack" style={{ "--gap": "8px" } as React.CSSProperties} aria-label="Log ind med en anden konto">
                  {sso.providers.map((p) => (
                    <button key={p.key} type="button" className="btn btn-outline btn-block" onClick={() => goProvider(p.key)} disabled={busy} style={{ gap: 10 }}>
                      <ProviderIcon kind={p.kind} />{p.label}
                    </button>
                  ))}
                  {sso.sso && !sso.providers.some((p) => p.kind === "saml") && (
                    <button type="button" className="btn btn-outline btn-block" style={{ gap: 10 }}
                            onClick={() => { setSsoHint(true); setChannel("email"); emailInput.current?.focus(); }}>
                      <ProviderIcon kind="saml" />Log ind med virksomhedens SSO
                    </button>
                  )}
                  {ssoHint && !sso.providers.some((p) => p.kind === "saml") && (
                    <p className="muted tiny" style={{ margin: 0, textAlign: "center" }}>Skriv din arbejdsmail ovenfor, så finder vi jeres login.</p>
                  )}
                </div>
              )}
              {supportsPasskey && !sso?.required && (
                <button type="button" className="btn btn-dark btn-block" onClick={passkeyLogin} disabled={busy}>
                  Log ind med passkey
                </button>
              )}
              {config?.demo_logins && demo.length > 0 && <DemoLogins accounts={demo} busy={busy} onPick={demoLogin} />}
              <details className="muted small">
                <summary style={{ cursor: "pointer" }}>Kan du ikke logge ind?</summary>
                <div className="stack" style={{ "--gap": "6px", paddingTop: 8 } as React.CSSProperties}>
                  <span>Der er ingen adgangskode at nulstille. Skriv din e-mail, så sender vi en ny kode og et login-link, som du bare klikker på.</span>
                  <span>Har du mistet din passkey, sikkerhedsnøgle eller YubiKey, kan en administrator gendanne din adgang.</span>
                  {sso?.required && (
                    <span>Nødadgang: <button type="button" className="btn btn-ghost btn-sm" style={{ padding: 0, height: "auto" }} onClick={() => sendCode()}>send en kode alligevel</button> (virker kun for superadministratoren).</span>
                  )}
                  {!signupOpen && (
                    <span>Har du ikke en konto, skal en administrator oprette dig{signupContact ? <>. Skriv til <strong>{signupContact}</strong></> : ""}.</span>
                  )}
                </div>
              </details>
            </form>
          )}

          {step.kind === "link" && (
            <div className="stack" style={{ "--gap": "16px" } as React.CSSProperties}>
              <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
                <div className="eyebrow">Login-link</div>
                <h2>Log ind med linket fra din mail</h2>
                <p className="muted small">Linket kan bruges én gang og udløber sammen med koden i mailen.</p>
              </div>
              <Alert>{error}</Alert>
              <button className="btn btn-primary btn-block" onClick={() => linkLogin(step.token)} disabled={busy} autoFocus>
                {busy ? "Logger ind …" : "Log ind"}
              </button>
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setError(null); setStep({ kind: "phone" }); }}>
                Bed om en ny kode i stedet
              </button>
            </div>
          )}

          {step.kind === "code" && (
            <form className="stack" style={{ "--gap": "16px" } as React.CSSProperties} onSubmit={verify}>
              <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
                <div className="eyebrow">Engangskode</div>
                <h2>Skriv koden</h2>
                <p className="muted small">
                  {signupOpen ? "Vi har sendt en kode til " : "Hvis der er en konto til "}{phone}
                  {signupOpen ? "" : ", har vi sendt en kode dertil"}
                  {channel === "email" ? ". Du kan også klikke på login-linket i mailen" : ""}. Koden kan kun bruges én gang og udløber om få minutter.
                </p>
                {!signupOpen && signupContact && <p className="muted tiny">Har du ikke en konto? Skriv til {signupContact}.</p>}
              </div>
              {step.devCode && <Alert kind="info">Udvikling: koden er <strong className="mono">{step.devCode}</strong>
                {step.devLink && <> · <a href={`/login#link=${step.devLink}`} onClick={(e) => { e.preventDefault(); setStep({ kind: "link", token: step.devLink! }); }}>brug login-linket</a></>}
              </Alert>}
              <input className="code-input" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code}
                     onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} aria-label="Engangskode" autoFocus />
              <Alert>{error}</Alert>
              <button className="btn btn-primary btn-block" disabled={busy || code.length !== 6}>Log ind</button>
              <div className="spread">
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setStep({ kind: "phone" })}>{channel === "email" ? "Skift e-mail" : "Skift nummer"}</button>
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
                  Næste gang logger du ind med fingeraftryk, ansigt eller enhedens kode, uden at vente på en mail.
                  {step.adminGraceLeft !== null && " Administrationen kræver en passkey, sikkerhedsnøgle eller godkendelsesapp efter "
                    + `${GRACE_LOGINS} logins med engangskode.`}
                </p>
              </div>
              {step.becameAdmin && <Alert kind="success">Du er den første bruger og er nu {user?.super_admin ? "superadministrator" : "administrator"}. Du finder administrationen under /admin.</Alert>}
              {step.adminGraceLeft !== null && <GracePills left={step.adminGraceLeft} />}
              {!supportsPasskey && <Alert kind="info">Din browser understøtter ikke passkeys. Du kan bruge en godkendelsesapp under Konto.</Alert>}
              <Alert>{error}</Alert>
              {supportsPasskey && <button className="btn btn-primary btn-block" onClick={createPasskey} disabled={busy}>Opret passkey nu</button>}
              <button className="btn btn-ghost btn-block" onClick={() => goFrom()}>
                {step.adminGraceLeft !== null && step.adminGraceLeft > 0
                  ? `Ikke nu (${step.adminGraceLeft} ${step.adminGraceLeft === 1 ? "login" : "logins"} til /admin tilbage)` : "Ikke nu"}
              </button>
            </div>
          )}

          {step.kind === "mfa" && (
            <div className="stack" style={{ "--gap": "16px" } as React.CSSProperties}>
              <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
                <div className="eyebrow">Totrinsbekræftelse</div>
                <h2>Bekræft at det er dig</h2>
                <p className="muted small">Din konto kræver en passkey, sikkerhedsnøgle eller kode fra din godkendelsesapp.</p>
              </div>
              <SecondFactor methods={step.methods} onDone={async () => { await refresh(); goFrom(); }} />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
