// Dashboard.jsx — the main “front door” page for Sentinel.AI
// This page loads aggregated compliance health data and shows a simple overview.

import React, { useEffect, useState } from "react";
import axios from "axios";

import DomainTile from "../components/DomainTile";

// Backend API base URL (keep this aligned with the port your FastAPI server runs on).
// We default to 8003 to avoid common local port conflicts.
const API_BASE_URL = "http://localhost:8003";

export default function Dashboard() {
  // Holds the full response from GET /dashboard/summary.
  const [summary, setSummary] = useState(null);

  // True while the page is fetching the dashboard summary.
  const [loading, setLoading] = useState(true);

  // Holds a human-readable error message if something fails.
  const [error, setError] = useState(null);

  // True while the Drive sync request is running.
  const [syncing, setSyncing] = useState(false);

  // Fetch the dashboard summary from the backend API.
  const fetchSummary = async () => {
    try {
      setError(null); // clear old errors so the user sees the latest state
      const res = await axios.get(`${API_BASE_URL}/dashboard/summary`);
      setSummary(res.data);
    } catch (e) {
      // Convert the error into a simple message a beginner can understand.
      const message =
        e?.response?.data?.error ||
        e?.message ||
        "Failed to load dashboard summary.";
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  // When the page first loads, fetch the summary once.
  useEffect(() => {
    fetchSummary();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Trigger a Drive sync, then re-fetch the dashboard summary.
  const handleSync = async () => {
    try {
      setError(null);
      setSyncing(true);

      // Ask the backend to sync and ingest Drive files.
      await axios.post(`${API_BASE_URL}/sync/drive`);

      // After syncing, refresh the summary so the UI updates.
      await fetchSummary();
    } catch (e) {
      const message =
        e?.response?.data?.error ||
        e?.message ||
        "Drive sync failed. Please try again.";
      setError(message);
    } finally {
      setSyncing(false);
    }
  };

  // Simple loading state so the user is never confused by an empty page.
  if (loading) {
    return (
      <div className="max-w-5xl mx-auto px-6 py-6">
        <div className="text-sm text-gray-600">Loading dashboard...</div>
      </div>
    );
  }

  // Convert an ISO datetime string into a friendly “human” label.
  // Takes: isoString (string or null). Returns: string like "07 May 09:30" or "—".
  const formatDateTime = (isoString) => {
    if (!isoString) return "—";

    try {
      const date = new Date(isoString);
      if (Number.isNaN(date.getTime())) return "—";

      // Use UK formatting since the project uses GBP and en-GB dates elsewhere in the UI.
      return date.toLocaleString("en-GB", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return "—";
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-6 py-6">
      {/* Show an error banner if the API request failed */}
      {error ? (
        <div className="bg-red-50 border border-red-200 text-red-800 rounded-lg p-4 mb-6 text-sm">
          {error}
        </div>
      ) : null}

      {/* Section 1 — Summary banner */}
      <div className="bg-white border rounded-lg p-5 mb-6 flex justify-between items-center">
        <div>
          <div className="text-lg font-medium">
            {(summary?.actions_summary?.total_pending ?? 0)} issues need your
            attention
          </div>
          <div className="text-sm text-gray-600">
            Estimated financial exposure: £
            {Number(summary?.financial_exposure_gbp ?? 0).toFixed(2)}
          </div>
        </div>

        <button
          onClick={handleSync}
          disabled={syncing}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-800 disabled:opacity-60"
        >
          {syncing ? "Syncing..." : "Sync Drive"}
        </button>
      </div>

      {/* Section 1b — Sync status (helps the user trust automation is running) */}
      <div className="bg-white border rounded-lg p-5 mb-6">
        <div className="text-lg font-medium mb-2">Sync status</div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="text-sm">
            <div className="text-gray-500">Last Drive sync</div>
            <div className="font-medium">
              {formatDateTime(summary?.last_drive_sync_at)}
            </div>
          </div>

          <div className="text-sm">
            <div className="text-gray-500">Next scheduled check</div>
            <div className="font-medium">
              {formatDateTime(summary?.next_scheduled_sync_at)}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              This is the backend monitoring loop (runs every 6 hours).
            </div>
          </div>
        </div>
      </div>

      {/* Section 2 — Domain tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
        {(summary?.domains || []).map((domain) => (
          <DomainTile key={domain.domain} domain={domain} />
        ))}
      </div>

      {/* Section 3 — Upcoming obligations timeline */}
      <div className="bg-white border rounded-lg p-5">
        <div className="text-lg font-medium mb-3">Next 90 days</div>

        {(summary?.upcoming_obligations || []).slice(0, 10).length === 0 ? (
          <div className="text-sm text-gray-500">
            No upcoming obligations found.
          </div>
        ) : (
          <div>
            {(summary?.upcoming_obligations || []).slice(0, 10).map((item, idx) => {
              const dueLabel = item?.due_date
                ? new Date(item.due_date).toLocaleDateString("en-GB", {
                    day: "2-digit",
                    month: "short",
                  })
                : "--";

              const type = (item?.obligation_type || "").toLowerCase();

              // Pick a badge style based on the obligation type.
              let badgeClass = "bg-gray-100 text-gray-700";
              if (type === "renewal") badgeClass = "bg-blue-100 text-blue-800";
              if (type === "cancellation_window" || type === "cancellation") {
                badgeClass = "bg-red-100 text-red-800";
              }

              return (
                <div
                  key={`${item?.document_id || "doc"}-${item?.due_date || idx}-${idx}`}
                  className="flex items-center gap-3 py-2 border-b border-gray-100 last:border-b-0"
                >
                  <div className="text-sm text-gray-500 w-16 flex-shrink-0">
                    {dueLabel}
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className="font-medium text-sm">
                      {item?.vendor_name || "Unknown vendor"}
                    </span>
                    <span className="text-sm text-gray-500">
                      {" "}
                      · {item?.description || "No description"}
                    </span>
                  </div>
                  <div
                    className={`text-xs px-2 py-0.5 rounded-full ${badgeClass}`}
                  >
                    {item?.obligation_type || "other"}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

