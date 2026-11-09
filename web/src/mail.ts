// Mail module (specs/mail): whether it is on for this instance and user. Used by the "Mail" tab in Mit område.
import { useEffect, useState } from "react";
import { api } from "./api";

export type MailStatus = { enabled: boolean; has_mailbox?: boolean; status?: string | null };

let cached: Promise<MailStatus> | null = null;

export function useMailStatus(): MailStatus | null {
  const [status, setStatus] = useState<MailStatus | null>(null);
  useEffect(() => {
    cached ??= api.get<MailStatus>("/api/mail/status").catch(() => ({ enabled: false }));
    let alive = true;
    cached.then((s) => alive && setStatus(s));
    return () => { alive = false; };
  }, []);
  return status;
}
