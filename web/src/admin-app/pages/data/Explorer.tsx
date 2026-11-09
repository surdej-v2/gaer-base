// Udforsk for one dataset: filters, sortable columns with the catalog's names, paging, masked fields and
// unmasking with a reason (POST /api/admin/data/datasets/:id/query and /unmask). Used by the dataset page and /data/explore.
import { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Lock, Plus, X } from "lucide-react";
import { api, errorText } from "../../../api";
import { Btn, Drawer, CloseBtn, ErrorBox, IconBtn, useToast } from "../../ui";
import { card, fmt, input, MONO, SANS, sectionLabel, select } from "../../theme";
import { cell, OPS, srOnly, type Dataset, type Filter, type QueryResult } from "./types";
import { ClsBadge } from "./parts";

const LIMIT = 50;
const time = new Intl.DateTimeFormat("da-DK", { timeStyle: "short" });

export default function Explorer({ dataset, onUnmasked }: { dataset: Dataset; onUnmasked?: () => void }) {
  const say = useToast();
  const [filters, setFilters] = useState<Filter[]>([]);
  const [applied, setApplied] = useState<Filter[]>([]);
  const [sort, setSort] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [row, setRow] = useState<Record<string, unknown> | null>(null);
  const [reason, setReason] = useState("");

  const run = useCallback(async () => {
    setLoading(true);
    try {
      const body = { filters: applied.filter((f) => f.field), sort, limit: LIMIT, offset };
      setResult(await api.post<QueryResult>(`/api/admin/data/datasets/${dataset.id}/query`, body));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [dataset.id, applied, sort, offset]);

  useEffect(() => {
    run();
  }, [run]);

  useEffect(() => {
    setFilters([]);
    setApplied([]);
    setSort(null);
    setOffset(0);
    setRow(null);
  }, [dataset.id]);

  const columns = result?.columns ?? [];
  const masked = columns.filter((c) => c.masked);
  const fieldOptions = dataset.fields;
  const where = (field: string) => result?.filters.find((f) => f.field === field)?.where;

  const apply = () => {
    setOffset(0);
    setApplied(filters.filter((f) => f.field));
  };

  const toggleSort = (name: string) => {
    setOffset(0);
    setSort(sort === name ? `-${name}` : sort === `-${name}` ? null : name);
  };

  const unmask = async () => {
    try {
      await api.post(`/api/admin/data/datasets/${dataset.id}/unmask`, { reason });
      setReason("");
      say("Maskerede felter vises i 15 minutter. Det er skrevet i audit-loggen");
      onUnmasked?.();
      await run();
    } catch (e) {
      say(errorText(e));
    }
  };

  const update = (i: number, patch: Partial<Filter>) => setFilters(filters.map((f, j) => (j === i ? { ...f, ...patch } : f)));

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
      <section aria-label="Rækker" style={{ ...card, flex: "999 1 560px", minWidth: 0, display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "12px 14px", borderBottom: "1px solid #e2e8f0", display: "flex", flexDirection: "column", gap: 8 }}>
          <span style={sectionLabel}>Filtre · alle skal gælde</span>
          {filters.map((f, i) => {
            const op = OPS.find((o) => o.op === f.op);
            const w = where(f.field);
            return (
              <div key={i} style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                <label style={{ display: "flex", flex: "1 1 160px" }}>
                  <span style={srOnly}>Felt</span>
                  <select value={f.field} onChange={(e) => update(i, { field: e.target.value })} style={{ ...select, width: "100%" }}>
                    <option value="">Vælg felt</option>
                    {fieldOptions.map((fo) => <option key={fo.name} value={fo.name}>{fo.label || fo.name}</option>)}
                  </select>
                </label>
                <label style={{ display: "flex", flex: "0 1 170px" }}>
                  <span style={srOnly}>Operator</span>
                  <select value={f.op} onChange={(e) => update(i, { op: e.target.value })} style={{ ...select, width: "100%" }}>
                    {OPS.map((o) => <option key={o.op} value={o.op}>{o.label}</option>)}
                  </select>
                </label>
                {!op?.noValue && (
                  <label style={{ display: "flex", flex: "1 1 160px" }}>
                    <span style={srOnly}>Værdi</span>
                    <input value={f.value} onChange={(e) => update(i, { value: e.target.value })} onKeyDown={(e) => e.key === "Enter" && apply()}
                      style={{ ...input, width: "100%" }} />
                  </label>
                )}
                {w && dataset.source_kind === "rest" && (
                  <span style={{ padding: "2px 8px", borderRadius: 9999, fontSize: 11, fontWeight: 600,
                    background: w === "api" ? "rgba(37,99,235,0.1)" : "#f1f5f9", color: w === "api" ? "#1d4ed8" : "#475569" }}>
                    {w === "api" ? "→ API-parameter" : "filtreres i Tørgær"}
                  </span>
                )}
                <IconBtn icon={X} label="Fjern filter" onClick={() => setFilters(filters.filter((_, j) => j !== i))} />
              </div>
            );
          })}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Btn icon={Plus} height={30} onClick={() => setFilters([...filters, { field: "", op: "contains", value: "" }])}>Filter</Btn>
            {(filters.length > 0 || applied.length > 0) && <Btn kind="primary" height={30} onClick={apply}>Anvend</Btn>}
          </div>
        </div>

        {error && <div style={{ padding: 14 }}><ErrorBox>{error}</ErrorBox></div>}
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, minWidth: Math.max(560, columns.length * 130) }}>
            <thead>
              <tr style={{ background: "#f8fafc", borderBottom: "1px solid #e2e8f0" }}>
                {columns.map((c) => {
                  const active = sort === c.name ? "asc" : sort === `-${c.name}` ? "desc" : null;
                  return (
                    <th key={c.name} scope="col" style={{ textAlign: "left", padding: "8px 12px", fontSize: 11, letterSpacing: "0.04em",
                      textTransform: "uppercase", color: active ? "#1d4ed8" : "#64748b", fontWeight: 700, whiteSpace: "nowrap" }}>
                      {c.masked ? (
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }} title="Maskeret felt">
                          {c.label}<Lock size={12} color="#b45309" aria-label="maskeret" />
                        </span>
                      ) : (
                        <button type="button" onClick={() => toggleSort(c.name)} style={{ all: "unset", cursor: "pointer", display: "inline-flex", alignItems: "center", gap: 4 }}>
                          {c.label}
                          {active === "asc" && <ArrowUp size={12} />}
                          {active === "desc" && <ArrowDown size={12} />}
                        </button>
                      )}
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {result?.rows.map((r, i) => (
                <tr key={i} className="tg-row" onClick={() => setRow(r)} style={{ borderBottom: "1px solid #f1f5f9", cursor: "pointer" }}>
                  {columns.map((c) => (
                    <td key={c.name} style={{ padding: "8px 12px", maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      color: c.masked ? "#94a3b8" : "#020817", fontFamily: c.masked ? MONO : SANS }}>
                      {cell(r[c.name])}
                    </td>
                  ))}
                </tr>
              ))}
              {result && result.rows.length === 0 && (
                <tr><td colSpan={columns.length || 1} style={{ padding: 16, color: "#64748b" }}>Ingen rækker.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, padding: "10px 14px", fontSize: 12, color: "#64748b" }}>
          <span>
            {loading ? "Henter …" : result ? (
              result.total !== null
                ? `${result.rows.length ? `${fmt(offset + 1)}–${fmt(offset + result.rows.length)} af ` : ""}${fmt(result.total)} rækker`
                : `Side ${Math.floor(offset / LIMIT) + 1}`
            ) : ""}
            {result?.source && ` · ${result.source.cached ? "fra cache" : `${result.source.ms} ms`}`}
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <Btn height={30} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>Forrige</Btn>
            <Btn height={30} disabled={!result?.has_more} onClick={() => setOffset(offset + LIMIT)}>Næste</Btn>
          </div>
        </div>
      </section>

      <aside aria-label="Maskering" style={{ flex: "1 1 280px", maxWidth: "100%", display: "flex", flexDirection: "column", gap: 12 }}>
        {result?.unmasked_until ? (
          <div style={{ ...card, padding: 16, display: "flex", flexDirection: "column", gap: 6 }}>
            <span style={sectionLabel}>Maskering</span>
            <span style={{ fontSize: 13 }}>Gule og røde felter vises for dig indtil kl. {time.format(new Date(result.unmasked_until))}.</span>
          </div>
        ) : masked.length > 0 ? (
          <div style={{ background: "#fffbeb", border: "1px solid #fde68a", borderRadius: 16, padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <span style={{ display: "flex", alignItems: "center", gap: 8, fontWeight: 600, color: "#78350f" }}>
              <Lock size={16} /> {masked.length === 1 ? "1 felt er maskeret" : `${masked.length} felter er maskeret`}
            </span>
            <p style={{ margin: 0, fontSize: 13, color: "#78350f", lineHeight: 1.5 }}>
              {masked.slice(0, 4).map((c) => c.label).join(", ")}{masked.length > 4 ? " …" : ""} er gule eller røde. Afmaskering gælder i 15
              minutter, kræver en årsag og skrives i audit-loggen. Maskerede felter kan ikke filtreres eller sorteres.
            </p>
            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 13, fontWeight: 500, color: "#78350f" }}>
              Årsag
              <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Fx GDPR-forespørgsel 2026-14"
                style={{ ...input, borderColor: "#fcd34d" }} />
            </label>
            <Btn onClick={unmask} disabled={reason.trim().length < 5} style={{ borderColor: "#b45309", color: "#78350f", justifyContent: "center" }}>
              Vis maskerede felter
            </Btn>
          </div>
        ) : null}
        {dataset.source_kind === "rest" && dataset.call && (
          <div style={{ ...card, padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
            <span style={sectionLabel}>Kald</span>
            <code style={{ font: `12px ${MONO}`, color: "#334155", wordBreak: "break-all" }}>{dataset.call.method} {dataset.call.path}</code>
            {Object.keys(dataset.call.params).length > 0 && (
              <span style={{ fontSize: 12, color: "#64748b" }}>
                Filtre med “er lig med” på {Object.values(dataset.call.params).map((p) => p.label).join(", ")} sendes til API'et. Andre filtre
                bruges på de hentede rækker.
              </span>
            )}
            {result?.source && <code style={{ font: `11px ${MONO}`, color: "#64748b", wordBreak: "break-all" }}>{result.source.url}</code>}
          </div>
        )}
      </aside>

      {row && (
        <Drawer width={420} onClose={() => setRow(null)} label="Række">
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 20px", borderBottom: "1px solid #e2e8f0" }}>
            <span style={{ fontWeight: 700 }}>Række</span>
            <CloseBtn onClick={() => setRow(null)} />
          </div>
          <dl style={{ margin: 0, padding: 20, overflowY: "auto", display: "grid", gridTemplateColumns: "auto 1fr", gap: "8px 14px", fontSize: 13 }}>
            {columns.map((c) => (
              <div key={c.name} style={{ display: "contents" }}>
                <dt style={{ color: "#64748b", display: "flex", flexDirection: "column", gap: 4 }}>
                  {c.label}<ClsBadge value={c.classification} />
                </dt>
                <dd style={{ margin: 0, wordBreak: "break-word", fontFamily: c.masked ? MONO : SANS }}>{cell(row[c.name]) || "—"}</dd>
              </div>
            ))}
          </dl>
        </Drawer>
      )}
    </div>
  );
}
