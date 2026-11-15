import { getEndpoint } from "./config.js";

const input = document.getElementById("endpoint");
input.value = await getEndpoint();

document.getElementById("save").addEventListener("click", async () => {
  try {
    const url = new URL(input.value);
    await chrome.storage.sync.set({ endpoint: url.origin });
    document.getElementById("status").textContent = "Gemt. Luk og åbn sidepanelet igen.";
  } catch {
    document.getElementById("status").textContent = "Ugyldig adresse";
  }
});

// Fallback for Mapper's import: if the side panel could not ask for the optional permission, it is asked for here.
const bmStatus = document.getElementById("bookmarks-status");
const bmWanted = { permissions: ["bookmarks"] };
const showBm = async () => {
  bmStatus.textContent = (await chrome.permissions.contains(bmWanted)) ? "Udvidelsen må læse dine bogmærker." : "";
};
await showBm();
document.getElementById("bookmarks").addEventListener("click", async () => {
  const ok = await chrome.permissions.request(bmWanted).catch(() => false);
  bmStatus.textContent = ok ? "Udvidelsen må læse dine bogmærker. Gå tilbage til Mapper og importér." : "Adgangen blev ikke givet.";
});
