// ActionQueue.jsx — shows all pending HITL actions and refreshes after user decisions.
import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import ActionCard from "../components/ActionCard";

// Backend API base URL (keep this aligned with the port your FastAPI server runs on).
// We default to 8003 to avoid common local port conflicts.
const API_BASE_URL = "http://localhost:8003";

// Lower number means higher priority in the queue.
const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

// Render the action queue page and keep it synced with the backend.
function ActionQueue() {
  // Stores pending action summaries returned by GET /actions.
  const [actions, setActions] = useState([]);
  // Controls loading text while the list is being fetched.
  const [loading, setLoading] = useState(true);
  // Shows a user-friendly fetch error if the API call fails.
  const [error, setError] = useState(null);
  // Shows a temporary success banner (for example after sending an action).
  const [successMessage, setSuccessMessage] = useState(null);
  // searchTerm filters cards by title/vendor text.
  const [searchTerm, setSearchTerm] = useState("");
  // severityFilter lets users focus on critical/high/medium/low quickly.
  const [severityFilter, setSeverityFilter] = useState("all");
  // sortMode chooses whether newest items or highest severity is prioritized.
  const [sortMode, setSortMode] = useState("severity");

  // Load the latest pending queue from the backend.
  const fetchActions = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await axios.get(`${API_BASE_URL}/actions`);
      setActions(Array.isArray(response.data) ? response.data : []);
    } catch (err) {
      setError("Failed to load actions. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  // Fetch once when this page first mounts.
  useEffect(() => {
    fetchActions();
  }, []);

  // Auto-hide success banner so it does not stay on screen forever.
  useEffect(() => {
    if (!successMessage) return undefined;
    const timer = setTimeout(() => setSuccessMessage(null), 4000);
    return () => clearTimeout(timer);
  }, [successMessage]);

  // Keep display order stable and severity-prioritized.
  const sortedActions = useMemo(() => {
    // Filter before sort so users only see relevant cards.
    const filtered = actions.filter((item) => {
      const severityMatches = severityFilter === "all" || (item?.severity || "") === severityFilter;
      const haystack = `${item?.title || ""} ${item?.summary || ""} ${item?.vendor_name || ""}`.toLowerCase();
      const searchMatches = !searchTerm.trim() || haystack.includes(searchTerm.trim().toLowerCase());
      return severityMatches && searchMatches;
    });

    // Support two common queue sort modes: risk-first or newest-first.
    return [...filtered].sort((a, b) => {
      if (sortMode === "newest") {
        const aTime = new Date(a?.created_at || 0).getTime();
        const bTime = new Date(b?.created_at || 0).getTime();
        return bTime - aTime;
      }
      const aRank = SEVERITY_ORDER[a?.severity] ?? 99;
      const bRank = SEVERITY_ORDER[b?.severity] ?? 99;
      if (aRank !== bRank) return aRank - bRank;
      const aTime = new Date(a?.created_at || 0).getTime();
      const bTime = new Date(b?.created_at || 0).getTime();
      return bTime - aTime;
    });
  }, [actions, searchTerm, severityFilter, sortMode]);

  // Handle child status updates, then refresh the list from source of truth.
  const handleStatusChange = (message) => {
    if (message) {
      setSuccessMessage(message);
    }
    fetchActions();
  };

  return (
    <div className="px-6 py-6">
      <h1 className="text-2xl font-medium">Action queue</h1>

      {successMessage ? (
        <div className="mt-4 rounded-md border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
          {successMessage}
        </div>
      ) : null}

      {/* Toolbar keeps queue exploration fast: search text, severity focus, and sort mode. */}
      <div className="mt-4 grid gap-2 md:grid-cols-3">
        <input
          type="text"
          value={searchTerm}
          onChange={(event) => setSearchTerm(event.target.value)}
          placeholder="Search vendor, title, or summary"
          className="rounded border border-gray-300 px-3 py-2 text-sm"
        />
        <select
          value={severityFilter}
          onChange={(event) => setSeverityFilter(event.target.value)}
          className="rounded border border-gray-300 px-3 py-2 text-sm"
        >
          <option value="all">All severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
        <select
          value={sortMode}
          onChange={(event) => setSortMode(event.target.value)}
          className="rounded border border-gray-300 px-3 py-2 text-sm"
        >
          <option value="severity">Sort: highest severity</option>
          <option value="newest">Sort: newest first</option>
        </select>
      </div>

      {loading ? <p className="mt-4">Loading...</p> : null}

      {!loading && error ? (
        <p className="mt-4 text-sm text-red-600">{error}</p>
      ) : null}

      {!loading && !error && sortedActions.length === 0 ? (
        <p className="mt-4 text-sm text-slate-600">
          No matching pending actions. Try clearing filters or upload a document to generate actions.
        </p>
      ) : null}

      {!loading && !error && sortedActions.length > 0 ? (
        <div className="mt-4 space-y-4">
          {sortedActions.map((action) => (
            <ActionCard
              key={action.id}
              action={action}
              onStatusChange={handleStatusChange}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default ActionQueue;
