"use client";

import type {
  WatchlistEntry,
  MonthlyRevenueRow,
  TickerDetail,
  ScraperHealth,
} from "@/lib/api/taiwanClient";
import WatchlistRevenueGrid from "./components/WatchlistRevenueGrid";
import TickerDrillDown from "./components/TickerDrillDown";
import TaiwanHealthIndicator from "./components/TaiwanHealthIndicator";

interface Props {
  watchlist: WatchlistEntry[];
  revenue: Record<string, MonthlyRevenueRow[]>;
  health: ScraperHealth[];
  isLoading: boolean;
  selectedTicker: string | null;
  selectedDetail: TickerDetail | null;
  onOpenTicker: (ticker: string) => void;
  onCloseDrillDown: () => void;
}

export default function TaiwanView({
  watchlist,
  revenue,
  health,
  isLoading,
  selectedTicker,
  selectedDetail,
  onOpenTicker,
  onCloseDrillDown,
}: Props) {
  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex items-center justify-between px-8 py-5 bg-white border-b border-slate-200 shrink-0">
        <div>
          <h1 className="text-xl font-bold text-slate-900 leading-tight">Taiwan</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            MOPS monthly revenue + material information for semi-ecosystem watchlist.
          </p>
        </div>
        <TaiwanHealthIndicator health={health} />
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {isLoading ? (
          <div className="text-sm text-slate-400">Loading...</div>
        ) : (
          <WatchlistRevenueGrid
            watchlist={watchlist}
            revenue={revenue}
            onOpenTicker={onOpenTicker}
          />
        )}
      </div>

      {selectedTicker && (
        <TickerDrillDown
          ticker={selectedTicker}
          detail={selectedDetail}
          history={revenue[selectedTicker] ?? []}
          onClose={onCloseDrillDown}
        />
      )}
    </div>
  );
}
