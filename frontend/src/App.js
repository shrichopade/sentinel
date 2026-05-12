import { useState } from "react";
import Dashboard from "./pages/Dashboard";
import DocumentVault from "./pages/DocumentVault";
import ActionQueue from "./pages/ActionQueue";
import ActivityLog from "./pages/ActivityLog";
import Chat from "./pages/Chat";

export default function App() {
  const [page, setPage] = useState("dashboard");

  const tabClass = (key) =>
    page === key
      ? "bg-white text-slate-900 shadow-sm font-semibold"
      : "bg-slate-800 text-slate-100 hover:bg-slate-700";

  return (
    <div className="min-h-screen bg-white text-slate-900">
      <main className="mx-auto max-w-5xl px-6 py-6">
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-800 bg-slate-900">
            <div className="flex flex-col gap-3 px-6 py-4 sm:flex-row sm:items-end sm:justify-between">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-xl font-bold tracking-tight text-white">
                  <img
                    src={`${process.env.PUBLIC_URL}/favicon.svg`}
                    alt="Sentinel"
                    className="h-7 w-7"
                  />
                  <span>Sentinel</span>
                </div>
                <div className="text-sm font-semibold text-slate-200">
                  Your Compliance and Governance Agent
                </div>
              </div>

              <nav className="flex flex-wrap items-center gap-2 text-sm">
                <button
                  onClick={() => setPage("dashboard")}
                  className={`rounded-lg px-3 py-1.5 transition-colors ${tabClass("dashboard")}`}
                >
                  Dashboard
                </button>
                <button
                  onClick={() => setPage("vault")}
                  className={`rounded-lg px-3 py-1.5 transition-colors ${tabClass("vault")}`}
                >
                  Document vault
                </button>
                <button
                  onClick={() => setPage("queue")}
                  className={`rounded-lg px-3 py-1.5 transition-colors ${tabClass("queue")}`}
                >
                  Action queue
                </button>
                <button
                  onClick={() => setPage("chat")}
                  className={`rounded-lg px-3 py-1.5 transition-colors ${tabClass("chat")}`}
                >
                  Chat
                </button>
                <button
                  onClick={() => setPage("activity")}
                  className={`rounded-lg px-3 py-1.5 transition-colors ${tabClass("activity")}`}
                >
                  Activity log
                </button>
              </nav>
            </div>
          </div>

          <div className="bg-slate-100">
            {page === "dashboard" && <Dashboard />}
            {page === "vault" && <DocumentVault />}
            {page === "queue" && <ActionQueue />}
            {page === "chat" && <Chat />}
            {page === "activity" && <ActivityLog />}
          </div>
        </div>
      </main>
    </div>
  );
}
