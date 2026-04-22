"use client";

// ---------------------------------------------------------------------------
// NotesContainer — SMART layer for the Notes list page.
// Fetches notes, handles create/delete, owns filter state.
// Renders NotesView (dumb) with typed props.
// ---------------------------------------------------------------------------

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { notesClient } from "@/lib/api/notesClient";
import {
  earningsClient,
  type EarningsReleaseStub,
  type EarningsReleaseDetail,
} from "@/lib/api/earningsClient";
import {
  researchClient,
  type ResearchQueryResponse,
} from "@/lib/api/researchClient";
import { useNotesStore } from "@/store/useNotesStore";
import { useNotesListStore } from "./store";
import NotesView from "./NotesView";

export default function NotesContainer() {
  const router = useRouter();
  const { notes, isLoading, setNotes, addNote, removeNote, setLoading } = useNotesStore();
  const {
    searchQuery, filterTicker, filterType, showCreateModal,
    setSearchQuery, setFilterTicker, setFilterType, setShowCreateModal,
  } = useNotesListStore();

  // Earnings releases — fetched once on mount and displayed as a separate
  // section in the Notes tab. Detail view is a modal (not a full route).
  const [earnings, setEarnings] = useState<EarningsReleaseStub[]>([]);
  const [earningsLoading, setEarningsLoading] = useState(false);
  const [openRelease, setOpenRelease] = useState<EarningsReleaseDetail | null>(null);
  const [openReleaseLoading, setOpenReleaseLoading] = useState(false);

  // Fetch on mount
  useEffect(() => {
    setLoading(true);
    notesClient.list({ limit: 100 }).then((res) => {
      if (res.success && res.data) setNotes(res.data);
    }).finally(() => setLoading(false));

    setEarningsLoading(true);
    earningsClient.list({ limit: 2000 })
      .then((res) => {
        if (res.success && res.data) {
          console.log(`[earnings] loaded ${res.data.length} releases`);
          setEarnings(res.data);
        } else {
          console.warn("[earnings] response missing success/data:", res);
        }
      })
      .catch((err) => {
        console.error("[earnings] fetch failed:", err);
      })
      .finally(() => setEarningsLoading(false));
  }, [setNotes, setLoading]);

  const handleOpenRelease = async (releaseId: string) => {
    setOpenReleaseLoading(true);
    try {
      const res = await earningsClient.get(releaseId);
      if (res.success && res.data) setOpenRelease(res.data);
    } finally {
      setOpenReleaseLoading(false);
    }
  };

  // Research query state — the question box at the top of the Notes tab.
  const [researchResult, setResearchResult] = useState<ResearchQueryResponse | null>(null);
  const [researchLoading, setResearchLoading] = useState(false);
  const [researchError, setResearchError] = useState<string | null>(null);

  const handleResearchQuery = async (ticker: string, question: string, lookbackYears: number) => {
    if (!ticker.trim() || !question.trim()) return;
    setResearchLoading(true);
    setResearchError(null);
    try {
      const res = await researchClient.query({
        ticker:         ticker.toUpperCase().trim(),
        question:       question.trim(),
        lookback_years: lookbackYears,
      });
      if (res.success && res.data) {
        setResearchResult(res.data);
      } else {
        setResearchError(res.error || "Query failed");
      }
    } catch (err) {
      setResearchError(err instanceof Error ? err.message : String(err));
    } finally {
      setResearchLoading(false);
    }
  };

  const handleClearResearch = () => {
    setResearchResult(null);
    setResearchError(null);
  };

  const handleCreate = async (payload: {
    title: string;
    note_type: string;
    company_tickers: string[];
    meeting_date?: string;
    ux_variant: "A" | "B";
  }) => {
    const res = await notesClient.create(payload);
    if (res.success && res.data) {
      addNote(res.data);
      setShowCreateModal(false);
      router.push(`/notes/${res.data.note_id}`);
    }
  };

  const handleDelete = async (noteId: string) => {
    const res = await notesClient.delete(noteId);
    if (res.success) removeNote(noteId);
  };

  // Client-side filtering
  const filtered = notes.filter((n) => {
    const matchesSearch =
      !searchQuery ||
      n.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      n.company_tickers.some((t) => t.toLowerCase().includes(searchQuery.toLowerCase()));
    const matchesTicker =
      !filterTicker || n.company_tickers.includes(filterTicker.toUpperCase());
    const matchesType = !filterType || n.note_type === filterType;
    return matchesSearch && matchesTicker && matchesType;
  });

  // Same filtering applied to earnings releases so the existing search/ticker
  // controls also work on the new section.
  const filteredEarnings = earnings.filter((e) => {
    const matchesSearch =
      !searchQuery ||
      e.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      e.ticker.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesTicker =
      !filterTicker || e.ticker === filterTicker.toUpperCase();
    // Note type filter doesn't apply to earnings releases — they're a separate
    // source kind. When the user picks a note_type filter, we hide earnings.
    const matchesType = !filterType;
    return matchesSearch && matchesTicker && matchesType;
  });

  return (
    <NotesView
      notes={filtered}
      isLoading={isLoading}
      searchQuery={searchQuery}
      filterTicker={filterTicker}
      filterType={filterType}
      showCreateModal={showCreateModal}
      earnings={filteredEarnings}
      earningsLoading={earningsLoading}
      openRelease={openRelease}
      openReleaseLoading={openReleaseLoading}
      onOpenRelease={handleOpenRelease}
      onCloseRelease={() => setOpenRelease(null)}
      researchResult={researchResult}
      researchLoading={researchLoading}
      researchError={researchError}
      onResearchQuery={handleResearchQuery}
      onClearResearch={handleClearResearch}
      onSearchChange={setSearchQuery}
      onFilterTickerChange={setFilterTicker}
      onFilterTypeChange={setFilterType}
      onOpenCreate={() => setShowCreateModal(true)}
      onCloseCreate={() => setShowCreateModal(false)}
      onCreate={handleCreate}
      onDelete={handleDelete}
      onOpen={(noteId) => router.push(`/notes/${noteId}`)}
    />
  );
}
