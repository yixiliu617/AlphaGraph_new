"use client";

// ---------------------------------------------------------------------------
// EngineView — PURE PRESENTATION.
// No API calls. No store imports. No global state.
// Receives everything it needs as props from EngineContainer.
// Can be rendered in isolation (Storybook, tests) with mock data.
// ---------------------------------------------------------------------------

import { Send, Loader2, Sparkles } from "lucide-react";
import AgentBlockRenderer from "@/components/domain/blocks/AgentBlockRenderer";
import type { Message } from "./store";

const SUGGESTED_QUERIES = [
  "AAPL Q4 Revenue Trend",
  "NVIDIA Bull Thesis",
  "Semi Sector Sentiment",
  "Catalyst Audit",
];

export interface EngineViewProps {
  messages: Message[];
  isProcessing: boolean;
  activeSessionId: string | null;
  query: string;
  onQueryChange: (value: string) => void;
  onSend: () => void;
}

export default function EngineView({
  messages,
  isProcessing,
  activeSessionId,
  query,
  onQueryChange,
  onSend,
}: EngineViewProps) {
  return (
    <div className="flex flex-col h-full space-y-4 p-6 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-semibold tracking-tight text-slate-900">
          Unified Data Engine
        </h2>
        <div className="flex items-center gap-2 text-[10px] font-mono text-slate-400">
          SESSION_ID: {activeSessionId || "NEW"}
        </div>
      </div>

      <div className="flex-1 flex gap-4 overflow-hidden">
        {/* Main Canvas */}
        <div className="flex-1 flex flex-col bg-slate-50/50 border border-slate-200 rounded-xl overflow-hidden shadow-sm relative">

          {/* Message area */}
          <div className="flex-1 p-6 space-y-8 overflow-y-auto">
            {messages.length === 0 ? (
              <div className="max-w-2xl mx-auto space-y-6 pt-20">
                <div className="text-center space-y-2">
                  <div className="flex justify-center mb-4">
                    <Sparkles className="text-slate-900" size={32} />
                  </div>
                  <h3 className="text-lg font-medium text-slate-900">
                    What can I analyze for you?
                  </h3>
                  <p className="text-sm text-slate-500">
                    I can route queries to DuckDB for math or Pinecone for sentiment.
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  {SUGGESTED_QUERIES.map((s) => (
                    <button
                      key={s}
                      onClick={() => onQueryChange(s)}
                      className="p-3 text-left text-xs bg-white border border-slate-200 rounded-lg hover:border-slate-900 hover:shadow-sm transition-all"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="max-w-4xl mx-auto space-y-8">
                {messages.map((msg) => (
                  <div key={msg.id} className="space-y-4">
                    <div className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                      <div
                        className={`max-w-[80%] p-4 rounded-2xl text-sm ${
                          msg.role === "user"
                            ? "bg-slate-900 text-white shadow-sm"
                            : "bg-white border border-slate-200 text-slate-800"
                        }`}
                      >
                        {msg.content}
                      </div>
                    </div>

                    {msg.blocks && msg.blocks.length > 0 && (
                      <div className="grid grid-cols-2 gap-4 ml-4">
                        {msg.blocks.map((block) => (
                          <div key={block.id} className="col-span-2 md:col-span-1 h-fit">
                            <AgentBlockRenderer block={block} />
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}

                {isProcessing && (
                  <div className="flex items-center gap-2 text-xs text-slate-400 font-mono animate-pulse">
                    <Loader2 size={12} className="animate-spin" />
                    Agent routing query...
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Input bar */}
          <div className="p-4 bg-white border-t border-slate-200">
            <div className="max-w-3xl mx-auto relative">
              <input
                value={query}
                onChange={(e) => onQueryChange(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && onSend()}
                disabled={isProcessing}
                className="w-full pl-4 pr-12 py-3 bg-slate-50 border border-slate-200 rounded-xl focus:ring-1 focus:ring-slate-400 outline-none text-sm disabled:opacity-50"
                placeholder="Ask AlphaGraph..."
              />
              <button
                onClick={onSend}
                disabled={isProcessing}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-2 bg-slate-900 text-white rounded-lg hover:bg-slate-800 transition-colors disabled:bg-slate-300"
              >
                <Send size={16} />
              </button>
            </div>
          </div>
        </div>

        {/* Saved Research sidebar */}
        <div className="w-80 flex flex-col gap-4">
          <div className="p-4 bg-white border border-slate-200 rounded-xl h-full shadow-sm flex flex-col">
            <h4 className="text-xs font-bold uppercase tracking-widest text-slate-500 mb-4">
              Saved Research
            </h4>
            <div className="flex-1 flex flex-col items-center justify-center border-2 border-dashed border-slate-100 rounded-lg">
              <p className="text-[10px] text-slate-400 font-mono italic text-center px-6">
                Drag blocks here to save them for Thesis Synthesis.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
