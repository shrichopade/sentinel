// ActionCard.jsx — displays one action queue item with full detail, review controls, and send confirmation.
import { useEffect, useState } from "react";
import axios from "axios";

// Backend API base URL (keep this aligned with the port your FastAPI server runs on).
// We default to 8003 to avoid common local port conflicts.
const API_BASE_URL = "http://localhost:8003";

// Tailwind color classes for each severity label.
const SEVERITY_STYLES = {
  critical: "bg-red-100 text-red-800",
  high: "bg-orange-100 text-orange-800",
  medium: "bg-yellow-100 text-yellow-800",
  low: "bg-green-100 text-green-800",
};

// Format date text in a readable way while staying lightweight.
function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString();
}

// Render one action card and manage approve/reject/edit/send interactions.
function ActionCard({ action, onStatusChange }) {
  const [detail, setDetail] = useState(null);
  const [isEditing, setIsEditing] = useState(false);
  const [draftContent, setDraftContent] = useState("");
  const [showModal, setShowModal] = useState(false);
  const [reasoningOpen, setReasoningOpen] = useState(
    action?.severity === "critical" || action?.severity === "high"
  );
  const [saving, setSaving] = useState(false);
  const [showRejectInput, setShowRejectInput] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [modalError, setModalError] = useState("");
  const [sent, setSent] = useState(false);
  // draftOpen controls the chevron accordion for long email content.
  const [draftOpen, setDraftOpen] = useState(false);
  // sourcesOpen keeps source chips collapsed by default for a cleaner card.
  const [sourcesOpen, setSourcesOpen] = useState(false);

  // Load full action details so this card can show draft/reasoning/sources/warnings.
  const fetchDetail = async () => {
    try {
      const response = await axios.get(`${API_BASE_URL}/actions/${action.id}`);
      const data = response.data || null;
      setDetail(data);
      setDraftContent(data?.draft_content || "");
    } catch (error) {
      console.error("[ActionCard] failed to fetch detail:", error);
      setDetail(null);
    }
  };

  // Fetch once when this card mounts or when the action id changes.
  useEffect(() => {
    fetchDetail();
  }, [action.id]);

  // Save edited draft text while leaving queue status as pending.
  const handleSaveDraft = async () => {
    setSaving(true);
    try {
      const response = await axios.put(`${API_BASE_URL}/actions/${action.id}/edit`, {
        draft_content: draftContent,
      });
      setDetail(response.data || detail);
      setIsEditing(false);
    } catch (error) {
      console.error("[ActionCard] failed to save draft:", error);
    } finally {
      setSaving(false);
    }
  };

  // Reject action (optional reason) and ask parent list to refresh.
  const handleReject = async () => {
    setSaving(true);
    try {
      await axios.put(`${API_BASE_URL}/actions/${action.id}/reject`, {
        reason: rejectReason || undefined,
      });
      onStatusChange?.();
    } catch (error) {
      console.error("[ActionCard] failed to reject action:", error);
    } finally {
      setSaving(false);
    }
  };

  // Confirm send flow: backend applies approve gate and returns success/error.
  const handleConfirmSend = async () => {
    setSaving(true);
    setModalError("");
    try {
      const response = await axios.post(`${API_BASE_URL}/actions/${action.id}/send`);
      if (response?.data?.status === "sent") {
        setSent(true);
        setShowModal(false);
        onStatusChange?.();
        return;
      }
      setModalError(response?.data?.detail || "Failed to send email.");
    } catch (error) {
      setModalError(error?.response?.data?.detail || error?.message || "Failed to send email.");
    } finally {
      setSaving(false);
    }
  };

  const severityStyle = SEVERITY_STYLES[action?.severity] || "bg-gray-100 text-gray-700";
  const warnings = Array.isArray(detail?.warnings) ? detail.warnings : [];
  const sources = Array.isArray(detail?.sources) ? detail.sources : [];
  const disabledActions = saving || sent;
  // Use detail first (fresh API detail), then summary list fallback.
  const generatedBy = detail?.generated_by || action?.generated_by || "model";
  // Show the continue button when the action was created by the fallback path.
  const canContinue = !sent && !saving && (generatedBy === "fallback");

  // Continue analysis: ask backend to resume and overwrite this action.
  const handleContinueAnalysis = async () => {
    setSaving(true);
    try {
      await axios.post(`${API_BASE_URL}/actions/${action.id}/continue`);
      onStatusChange?.("Analysis resumed. Refreshing the queue...");
      // Refresh this card detail so “fallback” can flip to “model-generated” after overwrite.
      setTimeout(() => {
        fetchDetail();
      }, 1500);
    } catch (error) {
      console.error("[ActionCard] failed to continue analysis:", error);
      onStatusChange?.("Failed to continue analysis. Please try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className={`rounded-full px-2 py-1 text-xs font-medium ${severityStyle}`}>
            {action?.severity}
          </span>
          {sent ? (
            <span className="rounded-full bg-green-100 px-2 py-1 text-xs font-medium text-green-700">
              Sent
            </span>
          ) : null}
          <span className="font-medium">{action?.vendor_name || "Unknown vendor"}</span>
          <span className="text-sm text-gray-500">{action?.doc_type || "contract"}</span>
          <span
            className={`rounded-full px-2 py-1 text-xs font-medium ${
              generatedBy === "fallback" ? "bg-slate-100 text-slate-700" : "bg-blue-100 text-blue-700"
            }`}
            title={
              generatedBy === "fallback"
                ? "Created by deterministic safety fallback."
                : "Created directly from model action planning."
            }
          >
            {generatedBy === "fallback" ? "Fallback-generated" : "Model-generated"}
          </span>
        </div>
        <span className="text-xs text-gray-400">{formatDate(action?.created_at)}</span>
      </div>

      <p className="mt-2 text-sm">{action?.summary || action?.title}</p>

      {warnings.length > 0 ? (
        <div className="mt-3 space-y-2">
          {warnings.map((warning, index) => (
            <div
              key={`${action.id}-warning-${index}`}
              className="rounded border border-amber-200 bg-amber-50 p-2 text-xs"
            >
              {warning}
            </div>
          ))}
        </div>
      ) : null}

      {detail?.escalate ? (
        <div className="mt-2 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700">
          {detail?.escalation_reason}
        </div>
      ) : null}

      <div className="mt-3">
        <button
          type="button"
          className="text-sm font-medium text-gray-700 underline"
          onClick={() => setReasoningOpen((prev) => !prev)}
        >
          {reasoningOpen ? "v Reasoning" : "> Reasoning"}
        </button>
        {reasoningOpen ? (
          <p className="mt-2 text-sm text-gray-600">{detail?.reasoning || "No reasoning available."}</p>
        ) : null}
      </div>

      <div className="mt-3">
        {/* Keep long draft body hidden until user expands it to reduce scrolling noise. */}
        <button
          type="button"
          className="text-sm font-medium text-gray-700 underline"
          onClick={() => setDraftOpen((prev) => !prev)}
        >
          {draftOpen ? "v Draft email content" : "> Draft email content"}
        </button>
        {draftOpen ? (
          <div className="mt-2">
            {isEditing ? (
              <div className="space-y-2">
                <textarea
                  className="h-48 w-full rounded border p-2 font-mono text-sm"
                  value={draftContent}
                  onChange={(event) => setDraftContent(event.target.value)}
                  disabled={saving}
                />
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-600"
                    onClick={() => {
                      setDraftContent(detail?.draft_content || "");
                      setIsEditing(false);
                    }}
                    disabled={saving}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="rounded bg-blue-600 px-3 py-1 text-sm text-white"
                    onClick={handleSaveDraft}
                    disabled={saving}
                  >
                    Save
                  </button>
                </div>
              </div>
            ) : (
              <pre className="whitespace-pre-wrap rounded bg-gray-50 p-3 text-sm">
                {detail?.draft_content || "No draft content available."}
              </pre>
            )}
          </div>
        ) : null}
      </div>

      {sources.length > 0 ? (
        <div className="mt-3">
          <button
            type="button"
            className="text-sm font-medium text-gray-700 underline"
            onClick={() => setSourcesOpen((prev) => !prev)}
          >
            {sourcesOpen ? `v Sources (${sources.length})` : `> Sources (${sources.length})`}
          </button>
          {sourcesOpen ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {sources.map((source, index) => {
                const text =
                  typeof source === "object"
                    ? String(source?.content || source?.text || JSON.stringify(source))
                    : String(source);
                return (
                  <span
                    key={`${action.id}-source-${index}`}
                    className="rounded-full bg-gray-100 px-2 py-1 text-xs"
                    title={text}
                  >
                    {text.slice(0, 50)}
                  </span>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}

      {showRejectInput ? (
        <div className="mt-3 space-y-2">
          <input
            type="text"
            value={rejectReason}
            onChange={(event) => setRejectReason(event.target.value)}
            placeholder="Optional rejection reason"
            className="w-full rounded border border-gray-300 p-2 text-sm"
            disabled={disabledActions}
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-600"
              onClick={() => {
                setShowRejectInput(false);
                setRejectReason("");
              }}
              disabled={disabledActions}
            >
              Cancel
            </button>
            <button
              type="button"
              className="rounded border border-red-200 px-3 py-1 text-sm text-red-600"
              onClick={handleReject}
              disabled={disabledActions}
            >
              Confirm reject
            </button>
          </div>
        </div>
      ) : null}

      <div className="mt-4 flex justify-end gap-2">
        {canContinue ? (
          <button
            type="button"
            className="rounded border border-slate-300 px-3 py-1 text-sm text-slate-700 disabled:opacity-50"
            onClick={handleContinueAnalysis}
            disabled={disabledActions}
            title="Resume analysis and upgrade this action with a full draft"
          >
            Continue analysis
          </button>
        ) : null}
        <button
          type="button"
          className="rounded border border-red-200 px-3 py-1 text-sm text-red-600 disabled:opacity-50"
          onClick={() => setShowRejectInput(true)}
          disabled={disabledActions}
        >
          Reject
        </button>
        <button
          type="button"
          className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-600 disabled:opacity-50"
          onClick={() => setIsEditing(true)}
          disabled={disabledActions}
        >
          Edit draft
        </button>
        <button
          type="button"
          className="rounded bg-blue-600 px-3 py-1 text-sm text-white disabled:opacity-50"
          onClick={() => setShowModal(true)}
          disabled={disabledActions}
        >
          Approve & send
        </button>
      </div>

      {showModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-40 p-4">
          <div className="w-full max-w-2xl rounded-lg bg-white p-4 shadow-lg">
            <p className="text-sm">
              Sending to: <span className="font-medium">{"{RESEND_FROM_EMAIL placeholder}"}</span>
            </p>
            <div className="mt-3 max-h-64 overflow-auto rounded border bg-gray-50 p-3 text-sm whitespace-pre-wrap">
              {draftContent}
            </div>
            <p className="mt-3 font-semibold text-red-700">This will send a real email.</p>
            {modalError ? <p className="mt-2 text-sm text-red-600">{modalError}</p> : null}
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-600"
                onClick={() => setShowModal(false)}
                disabled={saving}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded bg-blue-600 px-3 py-1 text-sm text-white disabled:opacity-50"
                onClick={handleConfirmSend}
                disabled={saving}
              >
                Confirm & send
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default ActionCard;
