import { create } from "zustand";
import { persist } from "zustand/middleware";
import { dataClient } from "@/lib/api/dataClient";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Ticker {
  symbol: string;
  name: string;
  sector: string;
  /** Derived from backend topline status — "built" | "building" | "unknown" */
  buildStatus?:   "built" | "building" | "unknown";
  lastPeriodEnd?: string | null;
  staleWarning?:  string | null;
}

interface UniverseState {
  tickers: Ticker[];
  sectors: string[];
  addTicker:       (ticker: Ticker)   => void;
  removeTicker:    (symbol: string)   => void;
  addSector:       (sector: string)   => void;
  removeSector:    (sector: string)   => void;
  setUniverse:     (tickers: Ticker[], sectors: string[]) => void;
  /** Pull topline build status from the backend and merge into local state. */
  syncFromBackend: () => Promise<void>;
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

const DEFAULT_SECTORS = [
  "Semiconductors",
  "Cloud Infrastructure",
  "Enterprise Software",
  "Consumer Technology",
  "Financials",
];

const DEFAULT_TICKERS: Ticker[] = [
  { symbol: "NVDA", name: "NVIDIA Corporation",    sector: "Semiconductors" },
  { symbol: "TSM",  name: "Taiwan Semiconductor",  sector: "Semiconductors" },
  { symbol: "MSFT", name: "Microsoft Corporation", sector: "Cloud Infrastructure" },
  { symbol: "AMZN", name: "Amazon.com Inc.",        sector: "Cloud Infrastructure" },
];

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useUniverseStore = create<UniverseState>()(
  persist(
    (set, get) => ({
      tickers: DEFAULT_TICKERS,
      sectors: DEFAULT_SECTORS,

      addTicker: (ticker) => {
        const upper = ticker.symbol.toUpperCase();
        if (get().tickers.some((t) => t.symbol.toUpperCase() === upper)) return;

        set((state) => ({
          tickers: [...state.tickers, { ...ticker, symbol: upper, buildStatus: "building" }],
        }));

        // Trigger topline build on the backend (fire-and-forget)
        dataClient.addTicker(upper).catch(() => {
          // Backend unreachable — status stays "building" until next syncFromBackend
        });
      },

      removeTicker: (symbol) =>
        set((state) => ({
          tickers: state.tickers.filter(
            (t) => t.symbol.toUpperCase() !== symbol.toUpperCase()
          ),
        })),

      addSector: (sector) =>
        set((state) => ({
          sectors: state.sectors.includes(sector)
            ? state.sectors
            : [...state.sectors, sector],
        })),

      removeSector: (sector) =>
        set((state) => ({ sectors: state.sectors.filter((s) => s !== sector) })),

      setUniverse: (tickers, sectors) => set({ tickers, sectors }),

      syncFromBackend: async () => {
        try {
          const status = await dataClient.getToplineStatus();
          const filingState = status.filing_state ?? {};

          set((state) => {
            // Update build status for tickers already in the local store
            const updated = state.tickers.map((t) => {
              const fs = filingState[t.symbol];
              if (!fs) return t;
              return {
                ...t,
                buildStatus:   fs.last_period_end ? ("built" as const) : ("building" as const),
                lastPeriodEnd: fs.last_period_end  ?? null,
                staleWarning:  fs.stale_warning    ?? null,
              };
            });

            // Backfill tickers that exist on the backend but not in local state
            // (e.g. added via CLI / another session)
            const localSymbols = new Set(state.tickers.map((t) => t.symbol.toUpperCase()));
            const backfilled: Ticker[] = Object.entries(filingState)
              .filter(([sym]) => !localSymbols.has(sym.toUpperCase()))
              .map(([sym, fs]) => ({
                symbol:        sym,
                name:          sym,       // company name unknown from backend-only entry
                sector:        "Unknown",
                buildStatus:   fs.last_period_end ? "built" : "building",
                lastPeriodEnd: fs.last_period_end ?? null,
                staleWarning:  fs.stale_warning   ?? null,
              }));

            return { tickers: [...updated, ...backfilled] };
          });
        } catch {
          // Backend not reachable — keep local state as-is
        }
      },
    }),
    { name: "alphagraph-universe" }
  )
);
