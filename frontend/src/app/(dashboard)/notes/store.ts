import { create } from "zustand";

interface NotesListStore {
  searchQuery: string;
  filterTicker: string;
  filterType: string;
  showCreateModal: boolean;
  setSearchQuery: (v: string) => void;
  setFilterTicker: (v: string) => void;
  setFilterType: (v: string) => void;
  setShowCreateModal: (v: boolean) => void;
}

export const useNotesListStore = create<NotesListStore>((set) => ({
  searchQuery: "",
  filterTicker: "",
  filterType: "",
  showCreateModal: false,
  setSearchQuery: (v) => set({ searchQuery: v }),
  setFilterTicker: (v) => set({ filterTicker: v }),
  setFilterType: (v) => set({ filterType: v }),
  setShowCreateModal: (v) => set({ showCreateModal: v }),
}));
