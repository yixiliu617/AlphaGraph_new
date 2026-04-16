import { create } from "zustand";

export interface Catalyst {
  catalyst_id: string;
  description: string;
  status: "pending" | "triggered" | "broken";
  impact_weight: number;
}

export interface Position {
  position_id: string;
  ticker: string;
  side: "long" | "short";
  summary: string;
  catalysts: Catalyst[];
  is_active: boolean;
}

export interface CatalystAlert {
  id: string;
  ticker: string;
  description: string;
  impact: string;
  timestamp: string;
}

interface LedgerState {
  positions: Position[];
  alerts: CatalystAlert[];
  isSyncing: boolean;
  setPositions: (positions: Position[]) => void;
  addAlert: (alert: CatalystAlert) => void;
  setSyncing: (isSyncing: boolean) => void;
}

export const useLedgerStore = create<LedgerState>((set) => ({
  positions: [],
  alerts: [],
  isSyncing: false,
  setPositions: (positions) => set({ positions }),
  addAlert: (alert) => set((state) => ({ 
    alerts: [alert, ...state.alerts] 
  })),
  setSyncing: (isSyncing) => set({ isSyncing }),
}));
