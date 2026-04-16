// ---------------------------------------------------------------------------
// Engine tab state — co-located with the feature that owns it.
// Nothing outside the engine tab should import from here directly.
// Global/shared state lives in src/store/.
// ---------------------------------------------------------------------------

import { create } from "zustand";

export interface AgentBlock {
  block_type: "chart" | "text" | "table" | "graph" | "financial_table";
  title: string;
  data: unknown;
  id: string;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  blocks?: AgentBlock[];
  id: string;
}

interface EngineState {
  messages: Message[];
  isProcessing: boolean;
  activeSessionId: string | null;
  addMessage:   (message: Omit<Message, "id">) => void;
  setProcessing:(isProcessing: boolean) => void;
  setSessionId: (id: string) => void;
  clearHistory: () => void;
}

export const useEngineStore = create<EngineState>((set) => ({
  messages:        [],
  isProcessing:    false,
  activeSessionId: null,

  addMessage: (message) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { ...message, id: Math.random().toString(36).substring(7) },
      ],
    })),

  setProcessing: (isProcessing) => set({ isProcessing }),
  setSessionId:  (id) => set({ activeSessionId: id }),
  clearHistory:  () => set({ messages: [], activeSessionId: null }),
}));
