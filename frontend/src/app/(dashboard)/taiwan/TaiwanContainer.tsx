"use client";

import { useCallback, useEffect, useState } from "react";
import {
  taiwanClient,
  type WatchlistEntry,
  type MonthlyRevenueRow,
  type TickerDetail,
  type ScraperHealth,
} from "@/lib/api/taiwanClient";
import TaiwanView from "./TaiwanView";

export default function TaiwanContainer() {
  const [watchlist, setWatchlist] = useState<WatchlistEntry[]>([]);
  const [revenue, setRevenue] = useState<Record<string, MonthlyRevenueRow[]>>({});
  const [health, setHealth] = useState<ScraperHealth[]>([]);
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<TickerDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    (async () => {
      setIsLoading(true);
      const [wlRes, hRes] = await Promise.all([taiwanClient.watchlist(), taiwanClient.health()]);
      if (wlRes.success && wlRes.data) setWatchlist(wlRes.data);
      if (hRes.success && hRes.data) setHealth(hRes.data.scrapers);

      if (wlRes.success && wlRes.data && wlRes.data.length > 0) {
        const tickers = wlRes.data.map((r) => r.ticker);
        const rev = await taiwanClient.monthlyRevenue(tickers, 24);
        if (rev.success && rev.data) {
          const grouped: Record<string, MonthlyRevenueRow[]> = {};
          for (const row of rev.data) {
            (grouped[row.ticker] ??= []).push(row);
          }
          setRevenue(grouped);
        }
      }
      setIsLoading(false);
    })();
  }, []);

  useEffect(() => {
    const id = setInterval(async () => {
      const h = await taiwanClient.health();
      if (h.success && h.data) setHealth(h.data.scrapers);
    }, 60_000);
    return () => clearInterval(id);
  }, []);

  const handleOpenTicker = useCallback(async (ticker: string) => {
    setSelectedTicker(ticker);
    const res = await taiwanClient.ticker(ticker);
    if (res.success && res.data) setSelectedDetail(res.data);
  }, []);

  const handleCloseDrillDown = useCallback(() => {
    setSelectedTicker(null);
    setSelectedDetail(null);
  }, []);

  return (
    <TaiwanView
      watchlist={watchlist}
      revenue={revenue}
      health={health}
      isLoading={isLoading}
      selectedTicker={selectedTicker}
      selectedDetail={selectedDetail}
      onOpenTicker={handleOpenTicker}
      onCloseDrillDown={handleCloseDrillDown}
    />
  );
}
