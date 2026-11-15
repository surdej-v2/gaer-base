// "Importér favoritter": Kilde → Vælg mapper → Placering → Færdig. The favourites are read in the browser (the
// extension's chrome.bookmarks over the bridge, or an exported .html file) and imported in one call that makes the
// folders and shortcuts in one transaction (POST /api/mapper/import). Shown the first time Mapper is empty.
import { Bookmark, FileUp, Pin, Sparkles, SquareDashed } from "lucide-react";
import { useMemo, useState } from "react";
import { errorText } from "../api";
import { extension } from "../bridge/extension";
import type { MetaNode } from "../tree/api";
import { TreeView, type CheckState } from "../tree/TreeView";
import type { TreeState } from "../tree/useTree";
import { Alert } from "../ui";
import { mapperApi, type ImportResult, type MapperState } from "./api";
import { countItems, fromChrome, fromHtml, plan, previewTree, roughUrl, walkFolders, type BmFolder, type PreviewNode } from "./bookmarks";

const STEPS = ["Kilde", "Vælg mapper", "Placering", "Færdig"];
// The narrow panel's guide has three steps and always imports into a new folder „Fra browseren“.
const COMPACT_STEPS = ["Kilde", "Vælg", "Færdig"];

/** The bookmark folders as a tree TreeView can show (it only needs ids, parents and labels). */
function localTree(folders: BmFolder[]): { tree: TreeState; idOf: Map<string, number>; byId: Map<number, BmFolder> } {
  const nodes = new Map<number, MetaNode>();
  const idOf = new Map<string, number>();
  const byId = new Map<number, BmFolder>();
  const kids = new Map<number | null, MetaNode[]>();
  let next = 1;
  walkFolders(folders, (f, parent) => {
    const id = next++;
    idOf.set(f.key, id);
    byId.set(id, f);
    const node: MetaNode = {
      id, parent_id: parent ? idOf.get(parent.key)! : null, path: "", display_path: "", slug: "", label: f.label, description: "",
      depth: 0, sort_order: id, dimension: "", private: true, archived: false, has_children: f.folders.length > 0,
    };
    nodes.set(id, node);
    const list = kids.get(node.parent_id) ?? [];
    list.push(node);
    kids.set(node.parent_id, list);
  });
  const tree: TreeState = {
    dimension: null, root: null, loading: false, error: null, truncated: false, byId: nodes,
    children: (p) => kids.get(p) ?? [], loaded: () => true, ensureChildren: async () => undefined, add: () => undefined, reload: () => undefined,
  };
  return { tree, idOf, byId };
}

function descendants(f: BmFolder): string[] {
  const out: string[] = [];
  walkFolders([f], (x) => out.push(x.key));
  return out;
}

export function ImportWizard({ state, inExtension, first, onDone, onCancel, compact = false }: {
  state: MapperState;
  compact?: boolean;
  inExtension: boolean;
  first: boolean;
  onDone: (result: ImportResult | null) => void;
  onCancel?: () => void;
}) {
  const [step, setStep] = useState(0);
  const [folders, setFolders] = useState<BmFolder[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [keepStructure, setKeepStructure] = useState(true);
  const [skipDuplicates, setSkipDuplicates] = useState(true);
  const [target, setTarget] = useState<"new" | "top">("new");
  const [targetLabel, setTargetLabel] = useState("Fra browseren");
  const [pinBar, setPinBar] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);

  const existing = useMemo(() => new Set(state.shortcuts.map((s) => roughUrl(s.url)).filter((u): u is string => !!u)), [state]);
  const local = useMemo(() => (folders ? localTree(folders) : null), [folders]);
  const opts = { selected, keepStructure, skipDuplicates, target, targetLabel, pinBar, existing };
  const current = useMemo(() => (folders ? plan(folders, opts) : null),
    [folders, selected, keepStructure, skipDuplicates, target, targetLabel, pinBar, existing]);
  const hasBar = useMemo(() => {
    let bar = false;
    if (folders) walkFolders(folders, (f) => { if (f.bar && selected.has(f.key)) bar = true; });
    return bar;
  }, [folders, selected]);

  const loaded = (list: BmFolder[]) => {
    setFolders(list);
    const all = new Set<string>();
    walkFolders(list, (f) => all.add(f.key));
    setSelected(all);
    setStep(1);
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const fromBrowser = () => run(async () => {
    const tree = await extension.bookmarks().catch((e: Error) => {
      if (/Ukendt kommando/.test(e.message)) throw new Error("Din browserudvidelse er for gammel til at læse bogmærker. Hent den nyeste under Konto, eller brug en eksportfil.");
      throw e;
    });
    const list = fromChrome(tree);
    if (!list.length) throw new Error("Der er ingen bogmærker i browseren");
    loaded(list);
  });

  const fromFile = (file: File | undefined) => file && run(async () => {
    if (file.size > 20 * 1024 * 1024) throw new Error("Filen er for stor (højst 20 MB)");
    loaded(fromHtml(await file.text()));
  });

  const empty = () => run(async () => {
    // Makes the root (~/mapper), so the guide does not come back; no folders, no shortcuts.
    onDone(await mapperApi.import({ target: "top", keep_structure: false, skip_duplicates: true, pin_bar: false, folders: [], items: [] }));
  });

  const doImport = () => current && run(async () => {
    if (current.items > 5000) throw new Error("Højst 5.000 genveje ad gangen. Vælg færre mapper.");
    setResult(await mapperApi.import(current.body));
    setStep(3);
  });

  const check = (n: MetaNode): CheckState => {
    const f = local!.byId.get(n.id)!;
    const keys = descendants(f);
    const on = keys.filter((k) => selected.has(k)).length;
    if (!selected.has(f.key)) return on ? "mixed" : "off";
    return on === keys.length ? "on" : "mixed";
  };
  const toggle = (n: MetaNode) => {
    const f = local!.byId.get(n.id)!;
    const keys = descendants(f);
    const turnOn = check(n) !== "on"; // a click fills a folder and everything below it, or empties it when full
    setSelected((old) => {
      const next = new Set(old);
      for (const k of keys) {
        if (turnOn) next.add(k);
        else next.delete(k);
      }
      return next;
    });
  };

  const summary = result ? `${result.shortcuts_added} genveje i ${result.folders_created + result.folders_reused} mapper` : "";

  return (
    <div className={`mp-pane${compact ? " mp-onboard" : ""}`} style={{ gap: 14 }}>
      <div className="mp-head">
        <h3>{first ? "Kom i gang med Mapper" : "Importér favoritter"}</h3>
        {onCancel && step < 3 && <button className="btn btn-ghost btn-sm" onClick={onCancel}>Annullér</button>}
      </div>
      <div className="mp-steps" aria-label="Trin">
        {(compact ? COMPACT_STEPS : STEPS).map((s, i) => {
          const at = compact && step === 3 ? 2 : step;
          return <span key={s} className={i === at ? "active" : i < at ? "done" : ""} aria-current={i === at ? "step" : undefined}>{i + 1}. {s}</span>;
        })}
      </div>

      {step === 0 && (
        <div className="stack" style={{ "--gap": "8px" } as React.CSSProperties}>
          {first && <p className="small muted" style={{ margin: 0 }}>Mapper samler dine genveje i dine egne mapper. Start med favoritterne fra din browser, eller begynd forfra.</p>}
          {/* Only the extension can read the browser's bookmarks; elsewhere (/m, a plain browser) only the exported file. */}
          {inExtension && (
            <button className="choice" disabled={busy} onClick={fromBrowser}>
              <Bookmark size={20} aria-hidden="true" />
              <span><strong>Fra denne browser</strong><br />
                <span className="small muted">Udvidelsen beder om lov til at læse dine bogmærker.</span></span>
            </button>
          )}
          <label className="choice" style={{ cursor: busy ? "default" : "pointer" }}>
            <FileUp size={20} aria-hidden="true" />
            <span><strong>Fra en eksportfil (.html)</strong><br />
              <span className="small muted">Eksportér bogmærker fra browserens bogmærkestyring, og vælg filen.</span></span>
            <input type="file" accept=".html,.htm,text/html" className="sr-only" disabled={busy} onChange={(e) => fromFile(e.target.files?.[0])} />
          </label>
          <button className="choice" disabled={busy} onClick={empty}>
            <SquareDashed size={20} aria-hidden="true" />
            <span><strong>Start med et tomt lager</strong><br /><span className="small muted">Du kan importere senere under „Importér favoritter“.</span></span>
          </button>
        </div>
      )}

      {step === 1 && local && current && (
        <div className="stack" style={{ "--gap": "10px" } as React.CSSProperties}>
          <p className="small muted" style={{ margin: 0 }}>Vælg de mapper, der skal med. En mappe tager kun sine egne bogmærker med; undermapper vælges for sig.</p>
          <div className="mp-scroll">
            <TreeView tree={local.tree} label="Mapper i dine bogmærker" check={check} onSelect={toggle} dense
              initiallyOpen={local.tree.children(null).map((n) => n.id)}
              aside={(n) => <span className="mp-count">{countItems(local.byId.get(n.id)!)}</span>} />
          </div>
          <label className="mp-opt"><input type="checkbox" checked={keepStructure} onChange={(e) => setKeepStructure(e.target.checked)} />
            <span>Bevar mappestrukturen<br /><span className="tiny muted">Ellers kommer alle genveje i én mappe.</span></span></label>
          <label className="mp-opt"><input type="checkbox" checked={skipDuplicates} onChange={(e) => setSkipDuplicates(e.target.checked)} />
            <span>Spring dubletter over{current.duplicates ? ` (${current.duplicates})` : " (ingen fundet)"}<br />
              <span className="tiny muted">Samme adresse to gange, eller en adresse, du allerede har i Mapper.</span></span></label>
          <label className="mp-opt" title="Kommer senere">
            <input type="checkbox" disabled checked={false} onChange={() => undefined} />
            <span className="muted"><Sparkles size={13} aria-hidden="true" /> Foreslå emner automatisk<br />
              <span className="tiny">Kommer senere. Du kan give genveje emner bagefter under „Ret“.</span></span></label>
          <p className="small" style={{ margin: 0 }}><strong>{current.items}</strong> genveje i <strong>{current.folders}</strong> mapper{current.invalid ? ` · ${current.invalid} kan ikke bruges (ikke http/https)` : ""}</p>
          {compact ? (
            <div className="mp-bottom">
              <button className="btn btn-primary btn-block mp-touch" disabled={busy || !current.items} onClick={doImport}>
                {busy ? "Importerer …" : `Importér ${current.items} genveje til „Fra browseren“`}
              </button>
              <button className="btn btn-ghost btn-block mp-touch" onClick={() => setStep(0)}>Tilbage</button>
            </div>
          ) : (
            <div className="row">
              <button className="btn btn-ghost btn-sm" onClick={() => setStep(0)}>‹ Tilbage</button>
              <button className="btn btn-primary btn-sm" disabled={!current.items} onClick={() => setStep(2)}>Næste</button>
            </div>
          )}
        </div>
      )}

      {step === 2 && current && (
        <div className="stack" style={{ "--gap": "10px" } as React.CSSProperties}>
          <fieldset style={{ border: 0, margin: 0, padding: 0 }} className="stack">
            <legend className="small" style={{ fontWeight: 600, marginBottom: 6 }}>Hvor skal de hen?</legend>
            <label className="mp-opt"><input type="radio" name="target" checked={target === "new"} onChange={() => setTarget("new")} />
              <span className="stack" style={{ "--gap": "6px", flex: 1 } as React.CSSProperties}>I en ny mappe
                {target === "new" && <input value={targetLabel} maxLength={120} onChange={(e) => setTargetLabel(e.target.value)} aria-label="Navn på mappen" />}
              </span></label>
            <label className="mp-opt"><input type="radio" name="target" checked={target === "top"} onChange={() => setTarget("top")} />
              <span>Øverst i Mapper</span></label>
          </fieldset>
          {keepStructure && hasBar && (
            <label className="mp-opt"><input type="checkbox" checked={pinBar} onChange={(e) => setPinBar(e.target.checked)} />
              <span>Fastgør Bogmærkelinjen<br /><span className="tiny muted">Den vises øverst i træet under „Fastgjort“.</span></span></label>
          )}
          <div>
            <span className="eyebrow">Sådan bliver det</span>
            <div className="mp-scroll" style={{ padding: "6px 8px" }}>
              <PreviewList nodes={previewTree(current)} />
            </div>
          </div>
          <div className="row">
            <button className="btn btn-ghost btn-sm" onClick={() => setStep(1)}>‹ Tilbage</button>
            <button className="btn btn-primary btn-sm" disabled={busy} onClick={doImport}>{busy ? "Importerer …" : `Importér ${current.items} genveje`}</button>
          </div>
        </div>
      )}

      {step === 3 && result && (
        <div className="stack" style={{ "--gap": "8px" } as React.CSSProperties}>
          <Alert kind="success"><strong>Færdig.</strong> {summary}.</Alert>
          <p className="small muted" style={{ margin: 0 }}>
            {result.folders_reused ? `${result.folders_reused} mapper fandtes i forvejen og blev brugt igen. ` : ""}
            {result.duplicates ? `${result.duplicates} dubletter sprunget over. ` : ""}
            {result.invalid ? `${result.invalid} bogmærker var ikke webadresser. ` : ""}
            {result.pinned ? "Bogmærkelinjen er fastgjort." : ""}
          </p>
          <div className={compact ? "mp-bottom" : undefined}>
            <button className={`btn btn-primary ${compact ? "btn-block mp-touch" : "btn-sm"}`} onClick={() => onDone(result)}>Åbn Mapper</button>
          </div>
        </div>
      )}

      <Alert>{error}</Alert>
    </div>
  );
}

function PreviewList({ nodes, depth = 0 }: { nodes: PreviewNode[]; depth?: number }) {
  if (!nodes.length) return depth ? null : <p className="small muted">Ingen mapper.</p>;
  const shown = nodes.slice(0, 60);
  return (
    <ul className="mp-ptree" style={depth ? undefined : { paddingLeft: 0 }}>
      {shown.map((n, i) => (
        <li key={i}>
          {n.pinned && <Pin size={12} aria-label="fastgjort" />} <strong>{n.label}</strong> <span className="mp-count">{n.count ? `${n.count}` : ""}</span>
          <PreviewList nodes={n.children} depth={depth + 1} />
        </li>
      ))}
      {nodes.length > shown.length && <li className="muted tiny">… og {nodes.length - shown.length} mere</li>}
    </ul>
  );
}
