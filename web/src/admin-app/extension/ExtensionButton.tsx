// Happy AI icon in the top bar, left of „Åbn appen“ (screen 11). The dot shows the bridge status.
import logo from "../assets/happy-mates-logo.svg";
import { happyAiUi, useHappyAi } from "./happyAiStore";

export default function ExtensionButton() {
  const { status, version, popoverOpen } = useHappyAi();
  const missing = status === "missing";
  const dot = status === "connected" ? "#10b981" : status === "outdated" ? "#f59e0b" : "#94a3b8";
  return (
    <button type="button" onClick={happyAiUi.togglePopover} aria-label="Happy AI-udvidelse"
      title={missing ? "Happy AI-udvidelsen er ikke installeret" : status === "unknown" ? "Happy AI-udvidelsen" : `Happy AI-udvidelsen · v${version}`}
      style={{ position: "relative", width: 32, height: 32, borderRadius: 9999, border: `1px solid ${popoverOpen ? "#2563eb" : "#e2e8f0"}`, background: "#ffffff",
        cursor: "pointer", padding: 0, display: "flex", alignItems: "center", justifyContent: "center",
        boxShadow: popoverOpen ? "0 0 0 2px rgba(37,99,235,0.25)" : "none" }}>
      <img src={logo} alt="" style={{ width: 22, height: 22, borderRadius: 9999, filter: missing ? "grayscale(1) opacity(0.6)" : "none" }} />
      <span style={{ position: "absolute", right: -1, bottom: -1, width: 10, height: 10, boxSizing: "border-box", borderRadius: 9999, border: "2px solid #ffffff", background: dot }} />
    </button>
  );
}
