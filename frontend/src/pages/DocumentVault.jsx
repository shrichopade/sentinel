// DocumentVault.jsx — the "Document Vault" page where users upload contract files.
// Users drop or click to upload a PDF or TXT file, which is sent to the backend for processing.
// Once processed, a card appears showing what Claude found in the document.

import { useEffect, useState } from 'react';
import axios from 'axios';

// The URL of our FastAPI backend server.
// Keep this aligned with the backend port you are currently running.
// We default to 8003 to avoid common local port conflicts.
const API = 'http://localhost:8003';

const DOMAIN_REGULATIONS = {
  subscription: "Consumer Contracts Regs 2013 · Consumer Rights Act 2015",
  employment: "Employment Rights Act 1996",
  tax: "Finance Act 2025",
  gdpr: "UK GDPR Articles 15–22",
  housing: "Tenancy Deposit Scheme · Landlord & Tenant Act 1985",
};

export default function DocumentVault() {
  // docs: list of successfully ingested documents shown as cards below the upload area
  const [docs, setDocs] = useState([]);

  // uploading: true while the file is being processed (shows "Processing..." text)
  const [uploading, setUploading] = useState(false);

  // statusIndex: which status message we show while uploading (cycles every 2 seconds)
  const [statusIndex, setStatusIndex] = useState(0);

  // error: holds an error message string if the upload fails, otherwise null
  const [error, setError] = useState(null);
  // tableDocs: list of all saved documents from backend for the tabular vault view
  const [tableDocs, setTableDocs] = useState([]);
  // tableLoading: true while loading documents for the table
  const [tableLoading, setTableLoading] = useState(true);
  // expandedDocId: tracks which single row is open to show the full summary text.
  const [expandedDocId, setExpandedDocId] = useState(null);

  const STATUS_MESSAGES = [
    "Extracting text...",
    "Classifying document...",
    "Creating embeddings...",
    "Extracting obligations...",
    "Finalising..."
  ]

  useEffect(() => {
    if (!uploading) { setStatusIndex(0); return; }
    const interval = setInterval(() => {
      setStatusIndex(i => Math.min(i + 1, STATUS_MESSAGES.length - 1));
    }, 2000);
    return () => clearInterval(interval);
  }, [uploading]);

  // Convert expiry date into active/expired/unknown for quick status scanning.
  function getDocumentStatus(expiryDate) {
    if (!expiryDate) return 'unknown';
    const expiry = new Date(expiryDate);
    if (Number.isNaN(expiry.getTime())) return 'unknown';
    const now = new Date();
    return expiry < now ? 'expired' : 'active';
  }

  // Fetch all saved documents so the table reflects what is currently in the vault.
  async function fetchDocumentsTable() {
    setTableLoading(true);
    try {
      const { data } = await axios.get(`${API}/documents`);
      setTableDocs(Array.isArray(data) ? data : []);
    } catch (err) {
      // Keep the rest of the page usable even if the table request fails.
      console.log('[DocumentVault] failed to fetch table data:', err.message);
      setTableDocs([]);
    } finally {
      setTableLoading(false);
    }
  }

  // Load the documents table once when this page opens.
  useEffect(() => {
    fetchDocumentsTable();
  }, []);

  // Called whenever the user selects a file via the file input
  async function handleFileChange(e) {
    // Get the first selected file from the input
    const file = e.target.files[0];
    if (!file) return;

    // Reset the input so the same file can be re-uploaded if needed
    e.target.value = '';

    // Clear any previous error and show the "Processing..." state
    setError(null);
    setUploading(true);

    try {
      // FormData is the standard way to send a file in an HTTP request
      const form = new FormData();
      form.append('file', file); // "file" must match the parameter name in the FastAPI endpoint

      // POST the file to the backend — this triggers the full ingestion pipeline
      const { data } = await axios.post(`${API}/ingest`, form);

      // On success, prepend the new document card to the top of the list
      // We spread 'data' (which contains doc_id, metadata, chunk_count, obligation_count)
      // and add the original filename so we can display it on the card
      setDocs(prev => [{ filename: file.name, ...data }, ...prev]);
      // Refresh the table so newly ingested files appear immediately.
      fetchDocumentsTable();
    } catch (err) {
      // If anything went wrong, show a human-readable error below the upload area
      setError('Upload failed: ' + err.message);
    } finally {
      // Always turn off the "Processing..." state, whether it succeeded or failed
      setUploading(false);
    }
  }

  return (
    <div>
      {/* Page heading */}
      <h1 className="text-2xl font-medium mb-6 max-w-2xl mx-auto px-6 pt-6">
        Document vault
      </h1>

      {/* Upload area — a styled label that triggers the hidden file input when clicked */}
      <label className="block border-2 border-dashed border-gray-300 rounded-lg p-10 text-center cursor-pointer hover:border-blue-400 transition-colors mb-6 max-w-2xl mx-auto px-6">
        {uploading ? (
          /* Shown while the backend is processing the file */
          <span className="text-sm text-blue-600">{STATUS_MESSAGES[statusIndex]}</span>
        ) : (
          /* Shown when idle, inviting the user to upload */
          <span className="text-sm text-gray-500">Drop a PDF here or click to upload</span>
        )}

        {/* Hidden file input — clicking the label above triggers this */}
        <input
          type="file"
          accept=".pdf,.txt"
          className="hidden"
          onChange={handleFileChange}
          disabled={uploading} // prevent a second upload while one is in progress
        />
      </label>

      {/* Error message — only shown if the upload failed */}
      {error && (
        <p className="text-sm text-red-500 max-w-2xl mx-auto px-6 mb-4">{error}</p>
      )}

      {/* Document cards — one card per successfully ingested document */}
      {docs.map((doc, i) => (
        <div key={doc.doc_id || i} className="max-w-2xl mx-auto px-6 mb-3">
          <div className="border rounded-lg p-4 bg-white shadow-sm">
            {/* Line 1: the original filename */}
            <p className="font-medium text-sm">{doc.filename}</p>

            {/* Line 2: quick stats — vendor, chunk count, obligation count */}
            <p className="text-sm text-gray-500 mt-1">
              {doc.metadata?.vendor_name || 'Unknown vendor'}
              {doc.is_duplicate ? (
                <>
                  {' · '}
                  Already in vault
                </>
              ) : (
                <>
                  {' · '}
                  {doc.chunk_count} chunks
                  {' · '}
                  {doc.obligation_count} obligations
                </>
              )}
            </p>

            {/* Line 3: the 2-sentence summary Claude generated */}
            {doc.metadata?.summary && (
              <p className="text-sm text-gray-400 mt-1 italic">{doc.metadata.summary}</p>
            )}
            {doc.metadata?.domain && DOMAIN_REGULATIONS[doc.metadata.domain] ? (
              <p className="text-xs text-blue-600 mt-1">
                Covered by: {DOMAIN_REGULATIONS[doc.metadata.domain]}
              </p>
            ) : null}
            {/* Duplicate hint — tells the user this upload was matched to an existing document */}
            {doc.is_duplicate && (
              <p className="text-xs text-amber-600 mt-1">
                Duplicate detected. Using existing document record.
              </p>
            )}
          </div>
        </div>
      ))}

      {/* Tabular vault view — shows all currently stored documents and classification details */}
      <div className="max-w-6xl mx-auto px-6 mt-8 mb-8">
        <h2 className="text-lg font-medium mb-3">Current documents</h2>
        {tableLoading ? (
          <p className="text-sm text-gray-500">Loading document table...</p>
        ) : tableDocs.length === 0 ? (
          <p className="text-sm text-gray-500">No documents available yet.</p>
        ) : (
          <div className="overflow-x-auto border rounded-lg bg-white shadow-sm">
            <table className="min-w-full text-sm">
              <thead className="bg-gray-50 text-gray-600">
                <tr>
                  <th className="text-left px-3 py-2 w-10">View</th>
                  <th className="text-left px-3 py-2">Filename</th>
                  <th className="text-left px-3 py-2">Vendor</th>
                  <th className="text-left px-3 py-2">Type</th>
                  <th className="text-left px-3 py-2">Domain</th>
                  <th className="text-left px-3 py-2">Flagged</th>
                  <th className="text-left px-3 py-2">Obligations</th>
                  <th className="text-left px-3 py-2">Effective</th>
                  <th className="text-left px-3 py-2">Expiry</th>
                  <th className="text-left px-3 py-2">Jurisdiction</th>
                  <th className="text-left px-3 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {tableDocs.map((doc, index) => {
                  const status = getDocumentStatus(doc.expiry_date);
                  // Use document id when available so expansion state stays stable across table refreshes.
                  const rowKey = doc.id || index;
                  // A row is expanded when its key matches expandedDocId.
                  const isExpanded = expandedDocId === rowKey;
                  const statusColor =
                    status === 'active'
                      ? 'bg-green-100 text-green-700'
                      : status === 'expired'
                        ? 'bg-red-100 text-red-700'
                        : 'bg-gray-100 text-gray-600';
                  return (
                    <>
                      <tr key={rowKey} className="border-t">
                        <td className="px-3 py-2 align-top">
                          {/* Chevron toggles the summary panel for this specific row. */}
                          <button
                            type="button"
                            className="text-gray-500 hover:text-gray-700"
                            aria-label={isExpanded ? 'Collapse summary' : 'Expand summary'}
                            onClick={() => setExpandedDocId(isExpanded ? null : rowKey)}
                          >
                            {isExpanded ? 'v' : '>'}
                          </button>
                        </td>
                        <td className="px-3 py-2">{doc.filename || '-'}</td>
                        <td className="px-3 py-2">{doc.vendor_name || '-'}</td>
                        <td className="px-3 py-2">{doc.doc_type || '-'}</td>
                        <td className="px-3 py-2">{doc.domain || '-'}</td>
                        <td className="px-3 py-2">{doc.flagged_clause_count ?? 0}</td>
                        <td className="px-3 py-2">{doc.obligation_count ?? 0}</td>
                        <td className="px-3 py-2">{doc.effective_date || '-'}</td>
                        <td className="px-3 py-2">{doc.expiry_date || '-'}</td>
                        <td className="px-3 py-2">{doc.jurisdiction || '-'}</td>
                        <td className="px-3 py-2">
                          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${statusColor}`}>
                            {status}
                          </span>
                        </td>
                      </tr>
                      {isExpanded && (
                        <tr className="border-t bg-gray-50">
                          {/* colSpan covers all table columns so summary has full width without wrapping the main row. */}
                          <td colSpan={11} className="px-4 py-3 text-sm text-gray-700">
                            <span className="font-medium text-gray-800">Summary: </span>
                            {doc.summary || 'No summary available for this document.'}
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
