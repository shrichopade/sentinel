// DomainTile.jsx — renders a single compliance domain “tile” on the Dashboard
// This shows a quick health indicator (green/amber/red) plus basic counts for that domain.

import React from "react";

// Map our backend domain keys to user-friendly display names.
const DOMAIN_DISPLAY_NAME = {
  subscription: "Subscriptions",
  employment: "Employment",
  tax: "Tax",
  gdpr: "GDPR / Privacy",
  housing: "Housing",
};

// Map our backend domain keys to simple single-letter “icons”.
const DOMAIN_ICON = {
  subscription: "S",
  employment: "E",
  tax: "T",
  gdpr: "G",
  housing: "H",
};

// Map health status to Tailwind classes for the border accent and dot.
const STATUS_STYLES = {
  green: { border: "border-green-400", dot: "bg-green-400" },
  amber: { border: "border-amber-400", dot: "bg-amber-400" },
  red: { border: "border-red-400", dot: "bg-red-400" },
};

// sendPrompt — best-effort hook to ask the app to open Chat with a prefilled prompt.
// Why: the Dashboard tile’s "View actions" link should guide the user without needing them to type.
function sendPrompt(text) {
  // If the app provides a global prompt handler, use it.
  if (typeof window !== "undefined" && typeof window.__sentinel_sendPrompt === "function") {
    window.__sentinel_sendPrompt(text);
    return;
  }

  // Fallback: emit a browser event so App.js (or another parent) can listen and route to Chat.
  if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
    window.dispatchEvent(new CustomEvent("sentinel:prompt", { detail: { text } }));
    return;
  }

  // Last resort: log (helps during development if wiring is not done yet).
  // eslint-disable-next-line no-console
  console.warn("sendPrompt not wired. Prompt was:", text);
}

export default function DomainTile({ domain }) {
  // Normalize the domain data so missing fields never crash the UI.
  const key = (domain?.domain || "").toLowerCase();
  const displayName = DOMAIN_DISPLAY_NAME[key] || "Unknown";
  const icon = DOMAIN_ICON[key] || (displayName[0] ? displayName[0].toUpperCase() : "?");
  const status = domain?.status || "green";
  const styles = STATUS_STYLES[status] || STATUS_STYLES.green;

  const docCount = Number(domain?.doc_count ?? 0);
  const openActions = Number(domain?.open_actions ?? 0);
  const avgRisk =
    typeof domain?.avg_risk === "number" ? domain.avg_risk.toFixed(1) : "—";

  return (
    <div
      className={`bg-white border-l-4 ${styles.border} rounded-lg p-4 border border-gray-100`}
    >
      {/* Top row: icon + name + status dot */}
      <div className="flex items-center">
        <div className="w-8 h-8 bg-gray-100 rounded-full text-xs font-medium flex items-center justify-center">
          {icon}
        </div>
        <div className="font-medium text-sm ml-2">{displayName}</div>
        <div className={`w-2 h-2 rounded-full ml-auto ${styles.dot}`} />
      </div>

      {/* Middle: small stat rows */}
      <div className="mt-3 space-y-1 text-sm">
        <div className="flex justify-between">
          <span className="text-gray-500">Documents</span>
          <span className="text-gray-900">{docCount}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Open actions</span>
          <span className={openActions > 0 ? "text-red-700 font-medium" : "text-gray-900"}>
            {openActions}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-gray-500">Risk score</span>
          <span className="text-gray-900">{avgRisk} out of 10</span>
        </div>
      </div>

      {/* Bottom: nudge to view actions when status is amber/red */}
      {status === "red" || status === "amber" ? (
        <button
          type="button"
          onClick={() => sendPrompt(`Show me open actions for the ${displayName} domain`)}
          className="text-xs text-blue-600 mt-3 block hover:underline"
        >
          View actions
        </button>
      ) : null}
    </div>
  );
}

