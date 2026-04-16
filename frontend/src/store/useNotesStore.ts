/**
 * Global notes list store.
 * Holds the flat list of NoteStubs — shared between the list page and sidebar.
 * Per-note editor state lives in /notes/[id]/store.ts (colocated).
 */

import { create } from "zustand";
import type { NoteStub } from "@/lib/api/notesClient";

interface NotesStore {
  notes: NoteStub[];
  isLoading: boolean;
  setNotes: (notes: NoteStub[]) => void;
  addNote: (note: NoteStub) => void;
  updateNote: (note: NoteStub) => void;
  removeNote: (noteId: string) => void;
  setLoading: (v: boolean) => void;
}

export const useNotesStore = create<NotesStore>((set) => ({
  notes: [],
  isLoading: false,

  setNotes: (notes) => set({ notes }),
  addNote: (note) => set((s) => ({ notes: [note, ...s.notes] })),
  updateNote: (note) =>
    set((s) => ({
      notes: s.notes.map((n) => (n.note_id === note.note_id ? note : n)),
    })),
  removeNote: (noteId) =>
    set((s) => ({ notes: s.notes.filter((n) => n.note_id !== noteId) })),
  setLoading: (v) => set({ isLoading: v }),
}));
