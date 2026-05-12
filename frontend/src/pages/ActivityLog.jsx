// ActivityLog.jsx — shows a chronological timeline of orchestrator steps for transparency.
import { useEffect, useState } from "react";
import axios from "axios";

// Backend API base URL (keep this aligned with the port your FastAPI server runs on).
// We default to 8003 to avoid common local port conflicts.
const API_BASE_URL = "http://localhost:8003";

// Convert timestamps into short relative strings for easy scanning.
function formatRelativeTime(value) {
  if (!value) return "";
  const created = new Date(value);
  if (Number.isNaN(created.getTime())) return String(value);

  const seconds = Math.floor((Date.now() - created.getTime()) / 1000);
  if (seconds < 60) return "just now";
  const mins = Math.floor(seconds / 60);
  if (mins < 60) return `${mins} mins ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} hours ago`;
  return created.toLocaleDateString();
}

// Render the system activity timeline from newest to oldest.
function ActivityLog() {
  // Stores the list of recorded orchestrator steps.
  const [entries, setEntries] = useState([]);
  // Controls loading text while API request is in progress.
  const [loading, setLoading] = useState(true);
  // Stores a lightweight error message if fetch fails.
  const [error, setError] = useState(null);

  // Fetch all activity rows once when the page mounts.
  useEffect(() => {
    const fetchActivity = async () => {
      setLoading(true);
      setError(null);
      try {
        const response = await axios.get(`${API_BASE_URL}/actions/activity`);
        setEntries(Array.isArray(response.data) ? response.data : []);
      } catch (err) {
        setError("Failed to load activity.");
      } finally {
        setLoading(false);
      }
    };

    fetchActivity();
  }, []);

  return (
    <div className="px-6 py-6">
      <h1 className="mb-6 text-2xl font-medium">Activity log</h1>

      {loading ? <p className="text-sm text-gray-600">Loading...</p> : null}
      {!loading && error ? <p className="text-sm text-red-600">{error}</p> : null}

      {!loading && !error && entries.length === 0 ? (
        <p className="text-sm text-gray-600">No activity recorded yet.</p>
      ) : null}

      {!loading && !error && entries.length > 0 ? (
        <div className="relative pl-8">
          <div className="absolute left-2 top-0 h-full w-px bg-gray-200" />
          {entries.map((entry, index) => {
            const isLast = index === entries.length - 1;
            const dotColor = entry?.is_autonomous ? "bg-gray-400" : "bg-blue-500";
            return (
              <div key={entry.id || `${entry.created_at}-${index}`} className="relative pb-4">
                <span
                  className={`absolute -left-1 top-1 h-[10px] w-[10px] rounded-full ${dotColor}`}
                />
                <div className="text-sm">
                  <div className="flex items-center justify-end">
                    <span className="text-xs text-gray-400">
                      {formatRelativeTime(entry?.created_at)}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <span className="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                      {entry?.agent_name || "orchestrator"}
                    </span>
                    <code className="text-gray-700">{entry?.tool_called || "unknown_tool"}</code>
                    <span
                      className={`rounded px-2 py-0.5 text-xs ${
                        entry?.is_autonomous
                          ? "bg-gray-100 text-gray-600"
                          : "bg-blue-100 text-blue-700"
                      }`}
                    >
                      {entry?.is_autonomous ? "Autonomous" : "Human review"}
                    </span>
                  </div>
                  <p className="mt-1 text-gray-600">{entry?.summary || "No summary available."}</p>
                  {entry?.link ? (
                    <a href={entry.link} className="mt-1 inline-block text-xs text-blue-500">
                      View document
                    </a>
                  ) : null}
                </div>
                {!isLast ? <div className="mt-4 border-b border-gray-200" /> : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

export default ActivityLog;
