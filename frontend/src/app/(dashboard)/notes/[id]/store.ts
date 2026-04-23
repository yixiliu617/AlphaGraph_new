import { create } from "zustand";
import type { NoteStub } from "@/lib/api/notesClient";

interface NoteEditorStore {
  note: NoteStub | null;
  isSaving: boolean;
  isDirty: boolean;
  showRecordingPopup: boolean;
  showUrlIngestModal: boolean;
  setNote: (note: NoteStub) => void;
  clearNote: () => void;
  setSaving: (v: boolean) => void;
  setDirty: (v: boolean) => void;
  setShowRecordingPopup: (v: boolean) => void;
  setShowUrlIngestModal: (v: boolean) => void;
  patchNote: (partial: Partial<NoteStub>) => void;
}

export const useNoteEditorStore = create<NoteEditorStore>((set) => ({
  note: null,
  isSaving: false,
  isDirty: false,
  showRecordingPopup: false,
  showUrlIngestModal: false,

  setNote: (note) => set({ note, isDirty: false }),
  clearNote: () => set({ note: null, isDirty: false }),
  setSaving: (v) => set({ isSaving: v }),
  setDirty: (v) => set({ isDirty: v }),
  setShowRecordingPopup: (v) => set({ showRecordingPopup: v }),
  setShowUrlIngestModal: (v) => set({ showUrlIngestModal: v }),
  patchNote: (partial) =>
    set((s) => ({
      note: s.note ? { ...s.note, ...partial } : s.note,
      isDirty: true,
    })),
}));
