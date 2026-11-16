// The AI's tools in Gær Developer (server/app/devkit/ai_tools.py). The browser runs them with the user's own session:
// reading tools get the same metadata the server's context has, proposals go to the open editor or the AI panel.
// Nothing is saved here — the user saves a proposal with the studio's own buttons (AI-2 in specs/developer/AI.md).
import { api } from "../../api";

type Propose = (tool: string, args: Record<string, unknown>) => Promise<string>;

export async function runDevTool(name: string, args: Record<string, unknown>, propose: Propose): Promise<string> {
  if (name === "catalog_search") {
    const r = await api.get<{ items: unknown[] }>(`/api/admin/dev/ai/search?q=${encodeURIComponent(String(args.q ?? ""))}`);
    return JSON.stringify(r.items);
  }
  if (name === "context_get") {
    const kind = String(args.kind ?? "");
    const id = Number(args.id);
    if (!["artifact", "dataset", "run"].includes(kind) || !Number.isFinite(id)) return "Fejl: kind og id mangler";
    const r = await api.get<{ text: string }>(`/api/admin/dev/ai/context?kind=${kind}&id=${id}&text=true`);
    return r.text;
  }
  if (name.startsWith("propose_")) return await propose(name, args);
  return "Værktøjet findes ikke her";
}

/** What a proposal is, in a line, and where the user saves it. */
export function proposalTitle(tool: string, args: Record<string, unknown>): { title: string; path?: string } {
  const id = args.artifact_id ? Number(args.artifact_id) : null;
  switch (tool) {
    case "propose_pipeline_change":
      return { title: "Ændring af pipeline", path: id ? `/developer/pipeline/${id}` : undefined };
    case "propose_testcases":
      return { title: `Testcases til /mcp/${String(args.endpoint ?? "")}`, path: `/developer/mcp/testbaenk?endpoint=${encodeURIComponent(String(args.endpoint ?? ""))}` };
    case "propose_doc_text":
      return { title: `Tekst til dokumentationen (${String(args.section ?? "")})`, path: id ? `/developer/api/dokumentation?api=${id}` : undefined };
    case "propose_flow_steps":
      return { title: "Trin til et flow", path: id ? `/developer/actions/${id}` : "/developer/actions/ny" };
    default:
      return { title: tool };
  }
}

/** The body of a proposal as text to read and copy. */
export function proposalBody(tool: string, args: Record<string, unknown>): string {
  if (tool === "propose_pipeline_change") return String(args.yaml ?? "");
  if (tool === "propose_doc_text") return String(args.text ?? "");
  if (tool === "propose_flow_steps") {
    try {
      return JSON.stringify(JSON.parse(String(args.definition ?? "")), null, 2);
    } catch {
      return String(args.definition ?? "");
    }
  }
  return JSON.stringify(tool === "propose_testcases" ? args.cases : args, null, 2);
}
