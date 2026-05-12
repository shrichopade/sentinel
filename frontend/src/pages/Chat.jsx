// Chat.jsx — chat interface with markdown rendering, expandable source cards, and document filenames

import { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';

// Backend API base URL (keep this aligned with the port your FastAPI server runs on).
// We default to 8003 to avoid common local port conflicts.
const API = 'http://localhost:8003';

// Status messages that cycle during the loading wait
const STATUS_STEPS = [
  { delay: 0,     text: 'Searching your documents...' },
  { delay: 4000,  text: 'Generating answer with Claude...' },
  { delay: 8000,  text: 'Still working — this can take up to 20 seconds on a free API plan...' },
  { delay: 16000, text: 'Almost there...' },
];

// SourceCard — a collapsible card showing the actual contract wording for one citation
function SourceCard({ source, docName }) {
  // open: whether this card is expanded to show the full excerpt
  const [open, setOpen] = useState(false);

  return (
    <div className="border border-gray-200 rounded-md mt-2 text-xs overflow-hidden">
      {/* Card header — always visible, click to toggle the excerpt */}
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-3 py-2 bg-gray-50 hover:bg-gray-100 text-left transition-colors"
      >
        <span className="font-medium text-gray-600">
          [{source.index}] {docName || (source.document_id ? `Doc ${source.document_id.slice(0, 8)}…` : 'Source')}
          {source.similarity > 0 && (
            <span className="ml-2 text-gray-400 font-normal">
              {Math.round(source.similarity * 100)}% match
            </span>
          )}
        </span>
        {/* Chevron icon rotates when card is open */}
        <span className={`text-gray-400 transition-transform ${open ? 'rotate-180' : ''}`}>▾</span>
      </button>

      {/* Expandable excerpt — the actual wording from the contract */}
      {open && (
        <div className="px-3 py-2 bg-white text-gray-600 leading-relaxed whitespace-pre-wrap border-t border-gray-100">
          {source.content}
        </div>
      )}
    </div>
  );
}

// AssistantBubble — renders the answer with markdown + expandable source cards
function AssistantBubble({ msg }) {
  // docNames: a map of document_id → filename, fetched from the backend
  const [docNames, setDocNames] = useState({});

  useEffect(() => {
    // For each unique document_id in the sources, fetch its filename from Supabase via the backend
    const ids = [...new Set(msg.sources?.map(s => s.document_id).filter(Boolean))];
    ids.forEach(async (id) => {
      try {
        const { data } = await axios.get(`${API}/documents/${id}`);
        setDocNames(prev => ({ ...prev, [id]: data.filename }));
      } catch {
        // If the lookup fails, fall back to a shortened ID
        setDocNames(prev => ({ ...prev, [id]: `Doc ${id?.slice(0, 8)}` }));
      }
    });
  }, [msg.sources]);

  return (
    <div className="bg-gray-50 text-gray-800 rounded-lg p-4 text-sm mr-12">
      {/* Render the answer as markdown so bold, bullets, and headings display properly */}
      <div className="prose prose-sm max-w-none">
        <ReactMarkdown>{msg.text}</ReactMarkdown>
      </div>

      {/* Source cards — one per cited chunk */}
      {msg.sources?.length > 0 && (
        <div className="mt-3 border-t border-gray-200 pt-3">
          <p className="text-xs text-gray-400 mb-1 font-medium uppercase tracking-wide">Sources</p>
          {msg.sources.map(s => (
            <SourceCard
              key={s.index}
              source={s}
              docName={docNames[s.document_id]}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function Chat() {
  const SESSION_ID = useRef("session_" + Math.floor(Date.now() / 1000)).current;
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [statusText, setStatusText] = useState('');
  const timers = useRef([]);
  // bottomRef: invisible element at the end of the message list we scroll into view
  const bottomRef = useRef(null);

  // On first load, pull any saved chat history for this session.
  useEffect(() => {
    (async () => {
      try {
        const response = await axios.get(`${API}/chat/history/${SESSION_ID}`);
        const history = response?.data?.messages;
        if (Array.isArray(history) && history.length > 0) {
          setMessages(history);
        }
      } catch {
        // If history fetch fails, just start with a blank conversation.
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-scroll to the bottom whenever a new message or the loading indicator appears
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  // Cycle through status messages while loading
  useEffect(() => {
    if (loading) {
      STATUS_STEPS.forEach(({ delay, text }) => {
        const id = setTimeout(() => setStatusText(text), delay);
        timers.current.push(id);
      });
    } else {
      timers.current.forEach(clearTimeout);
      timers.current = [];
      setStatusText('');
    }
  }, [loading]);

  function addMessage(msg) {
    setMessages(prev => [...prev, msg]);
  }

  async function send() {
    if (!input.trim()) return;
    const q = input.trim();
    setInput('');
    addMessage({ role: 'user', text: q });
    setLoading(true);

    try {
      const response = await axios.post(`${API}/chat`, { message: q, session_id: SESSION_ID });
      addMessage({
        role: 'assistant',
        text: response.data.answer,
        sources: response.data.sources,
      });
    } catch (err) {
      const msg = err.response?.data?.detail || err.message;
      const isRateLimit = msg?.toLowerCase().includes('rate');
      addMessage({
        role: 'error',
        text: isRateLimit
          ? 'Voyage AI rate limit reached. Please wait 20 seconds and try again, or add a payment method at dashboard.voyageai.com.'
          : 'Error: ' + msg,
      });
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') send();
  }

  return (
    <div className="px-6 py-6 flex flex-col" style={{ height: 'calc(100vh - 64px)' }}>
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-medium">Ask your documents</h1>
        <button
          type="button"
          className="text-xs text-gray-400 hover:text-red-500 cursor-pointer"
          onClick={async () => {
            try {
              await axios.delete(`${API}/chat/history/${SESSION_ID}`);
            } finally {
              setMessages([]);
            }
          }}
        >
          Clear conversation
        </button>
      </div>

      <div className="flex-1 overflow-y-auto space-y-3 mb-4">
        {messages.map((msg, i) => {
          if (msg.role === 'user') {
            return (
              <div key={i} className="bg-blue-50 text-blue-900 rounded-lg p-3 text-sm ml-12">
                {msg.text}
              </div>
            );
          }
          if (msg.role === 'assistant') {
            return <AssistantBubble key={i} msg={msg} />;
          }
          if (msg.role === 'error') {
            return (
              <div key={i} className="bg-red-50 text-red-700 rounded-lg p-3 text-sm">
                {msg.text}
              </div>
            );
          }
          return null;
        })}

        {/* Invisible anchor at the bottom — scrolled into view on every update */}
        <div ref={bottomRef} />

        {/* Spinning loading indicator with progressive status messages */}
        {loading && (
          <div className="bg-gray-50 rounded-lg p-3 text-sm mr-12 flex items-center gap-2">
            <svg className="animate-spin h-4 w-4 text-blue-400 shrink-0" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            <span className="text-gray-500 animate-pulse">{statusText}</span>
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <input
          type="text"
          className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
          placeholder="Ask a question about your documents..."
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          onClick={send}
          disabled={loading}
          className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Send
        </button>
      </div>
    </div>
  );
}
