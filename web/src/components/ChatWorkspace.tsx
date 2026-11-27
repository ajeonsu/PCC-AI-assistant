"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useChat } from "ai/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ChevronDown,
  ChevronRight,
  Database,
  ExternalLink,
  FileSpreadsheet,
  FileText,
  LogOut,
  Menu,
  MessageSquarePlus,
  Send,
  Sparkles,
  Wrench,
  X,
} from "lucide-react";
import { DEFAULT_MODEL_ID, MODEL_OPTIONS } from "@/lib/ai/models";
import { createBrowserSupabaseClient } from "@/lib/supabase/client";

interface IndexedFile {
  file_id: string;
  file_name: string;
  folder_path: string;
  mime_type: string;
  size_bytes: number;
  modified_time: string | null;
  web_view_link: string | null;
}

interface KnowledgeSummary {
  totalFiles: number;
  totalChunks: number;
  totalExcelTables: number;
  lastSyncAt: string | null;
  files: IndexedFile[];
}

const STARTER_PROMPTS = [
  "What was approved or discussed in the 90th PMC meeting PDF?",
  "Summarize the key points of the SBFP Milk Component Briefer.",
  "How many rows are in MFP_Main_Data_Cleaned_Suppliers.xlsx and what are the top suppliers?",
  "List all indexed files in my Google Drive folder and their sizes.",
];

export function ChatWorkspace({ userEmail }: { userEmail: string }) {
  const router = useRouter();
  const [modelId, setModelId] = useState<string>(DEFAULT_MODEL_ID);
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(true);
  const [knowledge, setKnowledge] = useState<KnowledgeSummary | null>(null);
  const [expandedTools, setExpandedTools] = useState<Record<string, boolean>>(
    {},
  );

  const {
    messages,
    input,
    handleInputChange,
    handleSubmit,
    setMessages,
    append,
    isLoading,
    error,
  } = useChat({
    api: "/api/chat",
    body: { modelId },
    maxSteps: 8,
  });

  useEffect(() => {
    fetch("/api/knowledge")
      .then((res) => (res.ok ? res.json() : null))
      .then((data: KnowledgeSummary | null) => {
        if (data) setKnowledge(data);
      })
      .catch(() => {});
  }, []);

  async function handleSignOut() {
    const supabase = createBrowserSupabaseClient();
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  function formatSize(bytes: number) {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${bytes} B`;
  }

  function toggleTool(key: string) {
    setExpandedTools((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  return (
    <div className="flex h-screen bg-slate-950 text-slate-100 overflow-hidden">
      {/* Sidebar */}
      <aside
        className={`${
          sidebarOpen ? "w-72" : "w-0"
        } transition-all duration-200 border-r border-slate-800 bg-slate-900/60 flex flex-col overflow-hidden shrink-0`}
      >
        <div className="p-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Database className="w-4 h-4 text-emerald-400" />
            <span className="font-semibold text-sm">Knowledge Base</span>
          </div>
          <button
            type="button"
            onClick={() => setSidebarOpen(false)}
            className="md:hidden text-slate-400 hover:text-white"
            aria-label="Close sidebar"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-3 grid grid-cols-3 gap-2 border-b border-slate-800 text-center">
          <div className="rounded-lg bg-slate-950/70 p-2 border border-slate-800/80">
            <div className="text-sm font-semibold text-white">
              {knowledge?.totalFiles ?? "—"}
            </div>
            <div className="text-[10px] text-slate-400">Files</div>
          </div>
          <div className="rounded-lg bg-slate-950/70 p-2 border border-slate-800/80">
            <div className="text-sm font-semibold text-emerald-400">
              {knowledge?.totalExcelTables ?? "—"}
            </div>
            <div className="text-[10px] text-slate-400">SQL Sheets</div>
          </div>
          <div className="rounded-lg bg-slate-950/70 p-2 border border-slate-800/80">
            <div className="text-sm font-semibold text-sky-400">
              {knowledge?.totalChunks ?? "—"}
            </div>
            <div className="text-[10px] text-slate-400">Doc Chunks</div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
          <div className="text-[11px] font-medium uppercase tracking-wider text-slate-500 px-1 mb-2">
            Indexed Drive Files
          </div>
          {knowledge?.files.map((file) => {
            const isSheet =
              file.file_name.toLowerCase().endsWith(".xlsx") ||
              file.file_name.toLowerCase().endsWith(".csv") ||
              file.mime_type.includes("spreadsheet");
            return (
              <a
                key={file.file_id}
                href={file.web_view_link ?? "#"}
                target="_blank"
                rel="noopener noreferrer"
                className="group flex items-start gap-2 rounded-lg p-2 hover:bg-slate-800/70 transition text-xs"
              >
                {isSheet ? (
                  <FileSpreadsheet className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                ) : (
                  <FileText className="w-4 h-4 text-sky-400 shrink-0 mt-0.5" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="text-slate-200 truncate group-hover:text-white">
                    {file.file_name}
                  </div>
                  <div className="text-[10px] text-slate-500">
                    {formatSize(file.size_bytes)}
                  </div>
                </div>
                <ExternalLink className="w-3 h-3 text-slate-600 group-hover:text-slate-300 shrink-0 mt-0.5" />
              </a>
            );
          })}
        </div>

        <div className="p-3 border-t border-slate-800 text-[11px] text-slate-400 flex items-center justify-between gap-2">
          <span className="truncate">{userEmail}</span>
          <button
            type="button"
            onClick={handleSignOut}
            className="inline-flex items-center gap-1 text-slate-400 hover:text-red-300 shrink-0"
            title="Sign out"
          >
            <LogOut className="w-3.5 h-3.5" />
            Out
          </button>
        </div>
      </aside>

      {/* Main Chat Area */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top Bar */}
        <header className="h-14 border-b border-slate-800 px-4 flex items-center justify-between gap-3 bg-slate-950/90">
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              onClick={() => setSidebarOpen((v) => !v)}
              className="p-1.5 rounded-lg border border-slate-800 text-slate-400 hover:text-white hover:bg-slate-900"
              aria-label="Toggle sidebar"
            >
              <Menu className="w-4 h-4" />
            </button>
            <span className="font-semibold text-sm sm:text-base text-white">
              PCC AI Assistant
            </span>
          </div>

          <div className="flex items-center gap-2">
            <select
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              className="rounded-lg bg-slate-900 border border-slate-700 px-2.5 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-emerald-500"
              aria-label="Select AI Model"
            >
              {MODEL_OPTIONS.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} ({m.badge})
                </option>
              ))}
            </select>

            <button
              type="button"
              onClick={() => setMessages([])}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900 hover:bg-slate-800 px-2.5 py-1.5 text-xs text-slate-200"
            >
              <MessageSquarePlus className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">New Chat</span>
            </button>
          </div>
        </header>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-6">
          <div className="max-w-3xl mx-auto space-y-5">
            {messages.length === 0 ? (
              <div className="pt-8 space-y-6">
                <div className="text-center space-y-2">
                  <div className="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 mb-1">
                    <Sparkles className="w-6 h-6" />
                  </div>
                  <h2 className="text-xl font-semibold text-white">
                    Ask anything about your Google Drive files
                  </h2>
                  <p className="text-sm text-slate-400 max-w-lg mx-auto">
                    Searches scanned PDFs, Word documents, and executes live SQL
                    queries against all {knowledge?.totalExcelTables ?? "45"}{" "}
                    Excel worksheets.
                  </p>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  {STARTER_PROMPTS.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => append({ role: "user", content: prompt })}
                      className="text-left rounded-xl border border-slate-800 bg-slate-900/60 hover:bg-slate-800/80 hover:border-slate-700 p-3.5 text-xs sm:text-sm text-slate-200 transition"
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m) => (
                <div
                  key={m.id}
                  className={`flex ${
                    m.role === "user" ? "justify-end" : "justify-start"
                  }`}
                >
                  <div
                    className={`max-w-[90%] rounded-2xl px-4 py-3 text-sm ${
                      m.role === "user"
                        ? "bg-emerald-600 text-white"
                        : "bg-slate-900 border border-slate-800 text-slate-100"
                    }`}
                  >
                    {m.role === "assistant" &&
                      m.toolInvocations &&
                      m.toolInvocations.length > 0 && (
                        <div className="mb-3 space-y-1.5">
                          {m.toolInvocations.map((inv) => {
                            const key = `${m.id}-${inv.toolCallId}`;
                            const isOpen = Boolean(expandedTools[key]);
                            return (
                              <div
                                key={inv.toolCallId}
                                className="rounded-lg border border-slate-800 bg-slate-950/70 text-xs"
                              >
                                <button
                                  type="button"
                                  onClick={() => toggleTool(key)}
                                  className="w-full flex items-center justify-between px-2.5 py-1.5 text-slate-300 hover:text-white"
                                >
                                  <span className="inline-flex items-center gap-1.5 font-mono text-[11px]">
                                    <Wrench className="w-3 h-3 text-emerald-400" />
                                    {inv.toolName}
                                    <span className="text-slate-500">
                                      ({inv.state === "result" ? "done" : "running"})
                                    </span>
                                  </span>
                                  {isOpen ? (
                                    <ChevronDown className="w-3.5 h-3.5" />
                                  ) : (
                                    <ChevronRight className="w-3.5 h-3.5" />
                                  )}
                                </button>
                                {isOpen && (
                                  <div className="px-2.5 pb-2 pt-1 border-t border-slate-800/80 space-y-1.5 overflow-x-auto">
                                    <pre className="text-[11px] text-slate-400">
                                      {JSON.stringify(inv.args, null, 2)}
                                    </pre>
                                    {inv.state === "result" && (
                                      <pre className="text-[11px] text-emerald-300/90 max-h-48 overflow-y-auto">
                                        {JSON.stringify(inv.result, null, 2)}
                                      </pre>
                                    )}
                                  </div>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}

                    {m.role === "user" ? (
                      <div className="whitespace-pre-wrap">{m.content}</div>
                    ) : (
                      <div className="markdown-body">
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          components={{
                            a: ({ ...props }) => (
                              <a
                                {...props}
                                target="_blank"
                                rel="noopener noreferrer"
                              />
                            ),
                          }}
                        >
                          {m.content || (isLoading ? "Thinking..." : "")}
                        </ReactMarkdown>
                      </div>
                    )}
                  </div>
                </div>
              ))
            )}

            {error && (
              <div className="rounded-xl border border-red-500/40 bg-red-950/40 px-4 py-3 text-xs text-red-300">
                Error: {error.message}. Try switching between Gemini 3.1 Flash
                Lite and Groq in the top-right dropdown if a provider hits a
                rate limit.
              </div>
            )}
          </div>
        </div>

        {/* Input Form */}
        <footer className="border-t border-slate-800 p-4 bg-slate-950">
          <form
            onSubmit={handleSubmit}
            className="max-w-3xl mx-auto flex items-center gap-2"
          >
            <input
              value={input}
              onChange={handleInputChange}
              placeholder="Ask about a PDF, Word briefer, or Excel spreadsheet..."
              disabled={isLoading}
              className="flex-1 rounded-xl bg-slate-900 border border-slate-700 px-4 py-3 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-emerald-500"
            />
            <button
              type="submit"
              disabled={isLoading || !input.trim()}
              className="rounded-xl bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 px-4 py-3 text-sm font-medium text-white inline-flex items-center gap-1.5 transition"
            >
              <Send className="w-4 h-4" />
              <span className="hidden sm:inline">Send</span>
            </button>
          </form>
        </footer>
      </div>
    </div>
  );
}
