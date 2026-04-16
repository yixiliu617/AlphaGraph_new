"use client";

// ---------------------------------------------------------------------------
// EngineContainer — SMART / DATA-FETCHING LAYER.
// Owns: API calls, store reads/writes, error handling.
// Renders: EngineView with typed props — zero UI logic here.
//
// Changing how data is fetched or which store is used never touches EngineView.
// Changing how the UI looks never touches this file.
// ---------------------------------------------------------------------------

import { useState } from "react";
import { useEngineStore } from "./store";
import { chatClient } from "@/lib/api/chatClient";
import EngineView from "./EngineView";

export default function EngineContainer() {
  const [query, setQuery] = useState("");

  const {
    messages,
    isProcessing,
    activeSessionId,
    addMessage,
    setProcessing,
    setSessionId,
  } = useEngineStore();

  const handleSend = async () => {
    if (!query.trim() || isProcessing) return;

    const userMessage = query;
    setQuery("");
    addMessage({ role: "user", content: userMessage });
    setProcessing(true);

    try {
      const response = await chatClient.query(userMessage, activeSessionId ?? undefined) as {
        success: boolean;
        data?: { answer: string; blocks: { block_type: string; title: string; data: unknown }[]; session_id: string };
        error?: string;
      };

      if (response.success && response.data) {
        const { answer, blocks, session_id } = response.data;
        setSessionId(session_id);
        addMessage({
          role: "assistant",
          content: answer,
          blocks: blocks.map((b) => ({ ...b, id: Math.random().toString(36).substring(7) })) as never,
        });
      } else {
        addMessage({
          role: "assistant",
          content: `Error: ${response.error ?? "Unknown error occurred"}`,
        });
      }
    } catch (err: unknown) {
      addMessage({
        role: "assistant",
        content: `Network Error: ${err instanceof Error ? err.message : String(err)}`,
      });
    } finally {
      setProcessing(false);
    }
  };

  return (
    <EngineView
      messages={messages}
      isProcessing={isProcessing}
      activeSessionId={activeSessionId}
      query={query}
      onQueryChange={setQuery}
      onSend={handleSend}
    />
  );
}
