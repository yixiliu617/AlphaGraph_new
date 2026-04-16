"use client";

/**
 * NoteSearchPanel — right-side panel on the note editor.
 * Broad search across ALL data_fragment sources with source filter dropdown.
 */

import { useState, useRef, useEffect } from "react";
import { Search, Send, ChevronDown, FileText, Mic, BarChart2, Newspaper } from "lucide-react";
import { chatClient } from "@/lib/api/chatClient";

const SOURCE_OPTIONS = [
  { value: "all", label: "All Sources", icon: Search },
  { value: "meeting_note", label: "Meeting Notes", icon: Mic },
  { value: "broker_report", label: "Broker Reports", icon: FileText },
  { value: "sec_filing", label: "SEC Filings", icon: BarChart2 },
  { value: "news", label: "News", icon: Newspaper },
];

const SUGGESTED_QUERIES = [
  "What did management say about capex guidance?",
  "How has gross margin trended over the last 4 quarters?",
  "What do broker reports say about competitive positioning?",
  "Suggest questions to ask management on the next call.",
];

interface Message {
  role: "user" | "assistant";
  content: string;
  source?: string;
}

interface Props {
  contextTickers: string[];
  contextNoteType: string;
}

export default function NoteSearchPanel({ contextTickers, contextNoteType }: Props) {
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");
  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [showSourceMenu, setShowSourceMenu] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const selectedSource = SOURCE_OPTIONS.find((s) => s.value === source) ?? SOURCE_OPTIONS[0];
  const SelectedIcon = selectedSource.icon;

  const handleSend = async (q?: string) => {
    const text = (q ?? query).trim();
    if (!text || isLoading) return;

    const contextPrefix = contextTickers.length
      ? `[Context: ${contextTickers.join(", ")} — ${contextNoteType.replace("_", " ")}] `
      : "";
    const fullQuery = contextPrefix + text;

    setMessages((m) => [...m, { role: "user", content: text, source }]);
    setQuery("");
    setIsLoading(true);

    try {
      const res = await chatClient.query(fullQuery, sessionId) as {
        success: boolean;
        data?: { answer: string; session_id: string };
        error?: string;
      };
      if (res.success && res.data) {
        setSessionId(res.data.session_id);
        setMessages((m) => [...m, { role: "assistant", content: res.data!.answer, source }]);
      } else {
        setMessages((m) => [...m, { role: "assistant", content: `Error: ${res.error ?? "Unknown"}`, source }]);
      }
    } catch (err) {
      setMessages((m) => [
        ...m,
        { role: "assistant", content: `Network error: ${err instanceof Error ? err.message : String(err)}` },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Panel header */}
      <div className="px-4 py-3 border-b border-slate-200 bg-white shrink-0">
        <p className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-2">Research Assistant</p>

        {/* Source picker */}
        <div className="relative mb-2">
          <button
            onClick={() => setShowSourceMenu((v) => !v)}
            className="flex items-center gap-2 w-full px-3 py-1.5 text-xs font-medium border border-slate-200 rounded-md hover:border-indigo-300 bg-slate-50 hover:bg-white transition-colors"
          >
            <SelectedIcon size={12} className="text-indigo-500" />
            <span className="text-slate-700">{selectedSource.label}</span>
            <ChevronDown size={12} className="text-slate-400 ml-auto" />
          </button>

          {showSourceMenu && (
            <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-20 overflow-hidden">
              {SOURCE_OPTIONS.map((opt) => {
                const Icon = opt.icon;
                return (
                  <button
                    key={opt.value}
                    onClick={() => { setSource(opt.value); setShowSourceMenu(false); }}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-indigo-50 hover:text-indigo-700 transition-colors ${
                      source === opt.value ? "text-indigo-700 font-semibold bg-indigo-50" : "text-slate-600"
                    }`}
                  >
                    <Icon size={12} />
                    {opt.label}
                  </button>
                );
              })}
            </div>
          )}
        </div>

        {/* Input */}
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder="Search or ask anything…"
            className="flex-1 px-3 py-1.5 text-xs border border-slate-200 rounded-md bg-slate-50 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500"
          />
          <button
            onClick={() => handleSend()}
            disabled={!query.trim() || isLoading}
            className="p-1.5 bg-indigo-600 text-white rounded-md hover:bg-indigo-700 disabled:opacity-40 transition-colors"
          >
            <Send size={13} />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.length === 0 && (
          <div className="space-y-2">
            <p className="text-[10px] font-bold text-slate-400 uppercase tracking-wider px-1">
              Suggested
            </p>
            {SUGGESTED_QUERIES.map((q) => (
              <button
                key={q}
                onClick={() => handleSend(q)}
                className="w-full text-left px-3 py-2 text-xs text-slate-600 bg-white border border-slate-200 rounded-lg hover:border-indigo-300 hover:bg-indigo-50 hover:text-indigo-700 transition-colors"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex flex-col gap-1 ${msg.role === "user" ? "items-end" : "items-start"}`}>
            {msg.role === "user" ? (
              <div className="max-w-[85%] px-3 py-2 text-xs bg-indigo-600 text-white rounded-xl rounded-tr-sm">
                {msg.content}
              </div>
            ) : (
              <div className="max-w-[95%] px-3 py-2 text-xs bg-white border border-slate-200 rounded-xl rounded-tl-sm text-slate-700 leading-relaxed whitespace-pre-wrap">
                {msg.content}
                {msg.source && msg.source !== "all" && (
                  <div className="mt-1.5 text-[9px] text-indigo-400 uppercase tracking-wide font-semibold">
                    Source: {SOURCE_OPTIONS.find((s) => s.value === msg.source)?.label}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}

        {isLoading && (
          <div className="flex items-start">
            <div className="px-3 py-2 bg-white border border-slate-200 rounded-xl rounded-tl-sm">
              <div className="flex gap-1">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="w-1.5 h-1.5 bg-indigo-400 rounded-full animate-bounce" style={{ animationDelay: `${i * 0.15}s` }} />
                ))}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
