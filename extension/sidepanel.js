// Side panel: shows <endpoint>/extension in an iframe and answers its bridge requests.
//
// Protocol (namespace "gaer-bridge"):
//   iframe → panel  { ns, id, direction: "iframe-to-host", type, payload }
//   panel → iframe  { ns, id, direction: "host-to-iframe", ok, data, error }
//   panel → iframe  { ns, direction: "host-to-iframe", type: "TAB_CHANGED", payload }   (push, no id)
//
// Only messages from the configured endpoint's origin are answered.

import { BRIDGE, VERSION, getEndpoint } from "./config.js";

const frame = document.getElementById("app");
const endpoint = await getEndpoint();
const origin = new URL(endpoint).origin;
frame.src = `${endpoint}/extension`;

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab;
}

function readable(tab) {
  return tab && /^https?:/.test(tab.url || "");
}

// Runs inside the page. Keep it self-contained: it is serialized by chrome.scripting.
function snapshot(maxChars) {
  const meta = (n) => document.querySelector(`meta[name="${n}"], meta[property="${n}"]`)?.content || "";
  const headings = [...document.querySelectorAll("h1, h2, h3")].slice(0, 30).map((h) => h.innerText.trim()).filter(Boolean);
  const main = document.querySelector("main, article, [role=main]") || document.body;
  return {
    url: location.href,
    title: document.title,
    description: meta("description") || meta("og:description"),
    headings,
    selection: String(window.getSelection() || "").trim(),
    text: (main.innerText || "").replace(/\n{3,}/g, "\n\n").slice(0, maxChars),
  };
}

function selectionOnly() {
  return String(window.getSelection() || "").trim();
}

async function inTab(fn, args = []) {
  const tab = await activeTab();
  if (!readable(tab)) throw new Error("Siden kan ikke læses (kun http og https)");
  const [result] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: fn, args });
  return result?.result;
}

const handlers = {
  GET_VERSION: async () => ({ version: VERSION, endpoint }),
  GET_PAGE_INFO: async () => {
    const tab = await activeTab();
    return { url: tab?.url || "", title: tab?.title || "", readable: readable(tab) };
  },
  GET_PAGE_SNAPSHOT: async (payload) => inTab(snapshot, [Math.min(payload?.maxChars || 5000, 50000)]),
  GET_SELECTION: async () => inTab(selectionOnly),
  OPEN_URL: async (payload) => {
    if (!/^https?:/.test(payload?.url || "")) throw new Error("Ugyldig adresse");
    await chrome.tabs.create({ url: payload.url });
    return true;
  },
  OPEN_OPTIONS: async () => chrome.runtime.openOptionsPage(),
  // Mapper's import of favourites. "bookmarks" is an optional permission: asked for here, while the click in the
  // iframe still counts as a user gesture (activation reaches the panel, the frame's parent). If the browser refuses
  // to ask from here, the options page has a button that does.
  GET_BOOKMARKS: async () => {
    const wanted = { permissions: ["bookmarks"] };
    let granted = await chrome.permissions.contains(wanted);
    if (!granted) {
      try {
        granted = await chrome.permissions.request(wanted);
      } catch {
        throw new Error("Giv udvidelsen adgang til bogmærker under Indstillinger for udvidelsen, og prøv igen");
      }
    }
    if (!granted) throw new Error("Du gav ikke udvidelsen adgang til dine bogmærker");
    const trim = (n) => ({ id: n.id, title: n.title, url: n.url, folderType: n.folderType, children: n.children?.map(trim) });
    return (await chrome.bookmarks.getTree()).map(trim);
  },
};

window.addEventListener("message", async (event) => {
  const msg = event.data;
  if (event.origin !== origin || event.source !== frame.contentWindow) return;
  if (!msg || msg.ns !== BRIDGE || msg.direction !== "iframe-to-host" || !msg.id) return;
  const reply = (body) => frame.contentWindow.postMessage({ ns: BRIDGE, id: msg.id, direction: "host-to-iframe", ...body }, origin);
  const handler = handlers[msg.type];
  if (!handler) return reply({ ok: false, error: `Ukendt kommando: ${msg.type}` });
  try {
    reply({ ok: true, data: await handler(msg.payload) });
  } catch (e) {
    reply({ ok: false, error: e?.message || String(e) });
  }
});

async function pushTab() {
  const tab = await activeTab();
  frame.contentWindow?.postMessage({
    ns: BRIDGE, direction: "host-to-iframe", type: "TAB_CHANGED",
    payload: { url: tab?.url || "", title: tab?.title || "", readable: readable(tab) },
  }, origin);
}
chrome.tabs.onActivated.addListener(pushTab);
chrome.tabs.onUpdated.addListener((_id, info) => { if (info.status === "complete" || info.title) pushTab(); });
