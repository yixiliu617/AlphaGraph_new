"use client";

import { useState, useEffect, useCallback } from "react";
import {
  dataClient,
  type DataRow,
  type SectorHeatmap,
  type SectorHeatmapDefinition,
  type SectorHeatmapMetric,
} from "@/lib/api/dataClient";
import {
  insightsClient,
  type MarginInsights,
  type MarginEditRequest,
} from "@/lib/api/insightsClient";
import DataExplorerView from "./DataExplorerView";

// ---------------------------------------------------------------------------
// Metrics to request from the backend
// ---------------------------------------------------------------------------

const METRICS = [
  // Income statement — full walk-down, top to bottom
  "revenue",
  "cost_of_revenue",
  "gross_profit",
  "gross_margin_pct",
  "rd_expense",
  "sga_expense",
  "opex",
  "operating_income",
  "operating_margin_pct",
  // Below the operating line (between op income and pretax)
  "interest_expense",
  "interest_income",
  "other_income_net",
  "pretax_income",
  "income_tax",
  // Bottom line
  "net_income",
  "net_margin_pct",
  "eps_basic",
  "eps_diluted",
  "shares_basic",
  "shares_diluted",
  // Cash flow
  "operating_cf",
  "investing_cf",
  "financing_cf",
  "capex",
  "depreciation",
  "ebitda",
  "free_cash_flow",
  // Temporal — only available when calculated layer is built
  "revenue_yoy_pct",
  "revenue_qoq_pct",
  "gross_profit_yoy_pct",
  "operating_income_yoy_pct",
  "operating_income_qoq_pct",
  "net_income_yoy_pct",
  "net_income_qoq_pct",
  "eps_diluted_yoy_pct",
  // Margin deltas (percentage-point YoY difference) — backend-computed
  "gross_margin_pct_diff_yoy",
  "operating_margin_pct_diff_yoy",
  "net_margin_pct_diff_yoy",
];

const LOOKBACK_YEARS = 6;

// ---------------------------------------------------------------------------
// Container
// ---------------------------------------------------------------------------

export default function DataExplorerContainer() {
  const [loadedTickers, setLoadedTickers] = useState<string[]>(["NVDA"]);
  const [activeTicker, setActiveTicker]   = useState("NVDA");
  const [allRows, setAllRows]             = useState<DataRow[]>([]);
  const [loading, setLoading]             = useState(false);
  const [error, setError]                 = useState<string | null>(null);
  const [apiWarnings, setApiWarnings]     = useState<string[]>([]);

  // Phase B: qualitative margin insights per ticker.
  // Keyed by ticker so switching tabs doesn't lose already-fetched narratives.
  const [insightsByTicker, setInsightsByTicker] = useState<Record<string, MarginInsights>>({});
  const [insightsLoading,  setInsightsLoading]  = useState(false);
  const [insightsError,    setInsightsError]    = useState<string | null>(null);

  // Fetch data for all currently loaded tickers
  const fetchAll = useCallback(async (tickers: string[]) => {
    if (!tickers.length) return;
    setLoading(true);
    setError(null);
    try {
      const result = await dataClient.fetch({
        tickers,
        metrics: METRICS,
        period: "quarterly",
        lookback_years: LOOKBACK_YEARS,
      });
      setAllRows(result.rows);
      setApiWarnings(result.warnings);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial load
  useEffect(() => {
    fetchAll(loadedTickers);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Fetch margin insights for the active ticker once its data rows exist.
  // Skipped if we already have a cached narrative for this ticker.
  useEffect(() => {
    if (!activeTicker) return;
    if (insightsByTicker[activeTicker]) {
      setInsightsError(null);
      return;
    }
    const hasRows = allRows.some((r) => r.ticker === activeTicker);
    if (!hasRows) return;

    let cancelled = false;
    setInsightsLoading(true);
    setInsightsError(null);
    insightsClient
      .getMarginInsights(activeTicker)
      .then((res) => {
        if (cancelled) return;
        setInsightsByTicker((prev) => ({ ...prev, [activeTicker]: res }));
      })
      .catch((err) => {
        if (cancelled) return;
        setInsightsError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setInsightsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [activeTicker, allRows, insightsByTicker]);

  const handleRefreshInsights = useCallback(async () => {
    if (!activeTicker) return;
    setInsightsLoading(true);
    setInsightsError(null);
    try {
      const res = await insightsClient.refreshMarginInsights(activeTicker);
      setInsightsByTicker((prev) => ({ ...prev, [activeTicker]: res }));
    } catch (err) {
      setInsightsError(err instanceof Error ? err.message : String(err));
    } finally {
      setInsightsLoading(false);
    }
  }, [activeTicker]);

  const handleEditInsights = useCallback(
    async (edit: MarginEditRequest) => {
      if (!activeTicker) return;
      setInsightsError(null);
      try {
        const res = await insightsClient.editMarginInsights(activeTicker, edit);
        setInsightsByTicker((prev) => ({ ...prev, [activeTicker]: res }));
      } catch (err) {
        setInsightsError(err instanceof Error ? err.message : String(err));
      }
    },
    [activeTicker],
  );

  // Handle adding a new ticker
  const handleAddTicker = useCallback(async (symbol: string) => {
    const upper = symbol.toUpperCase().trim();
    if (!upper || loadedTickers.includes(upper)) return;

    // Optimistically add the tab so the user sees immediate feedback
    const nextTickers = [...loadedTickers, upper];
    setLoadedTickers(nextTickers);
    setActiveTicker(upper);

    // Trigger background build on the server (no-op if already built)
    dataClient.addTicker(upper).catch(() => {
      // Build might already exist — ignore the error, fetch will reveal truth
    });

    // Fetch data (will return empty if build is still in progress)
    await fetchAll(nextTickers);
  }, [loadedTickers, fetchAll]);

  const handleRemoveTicker = useCallback((symbol: string) => {
    const next = loadedTickers.filter((t) => t !== symbol);
    setLoadedTickers(next);
    if (activeTicker === symbol) setActiveTicker(next[0] ?? "");
    setAllRows((prev) => prev.filter((r) => r.ticker !== symbol));
  }, [loadedTickers, activeTicker]);

  // Rows for the currently active ticker only
  const activeRows = allRows.filter((r) => r.ticker === activeTicker);

  const activeInsights = insightsByTicker[activeTicker] ?? null;

  // Sector heatmap state
  const [heatmapDefinitions, setHeatmapDefinitions] = useState<SectorHeatmapDefinition[]>([]);
  const [heatmapGroupDef, setHeatmapGroupDef]       = useState("GICS_industry");
  const [heatmapMetric, setHeatmapMetric]           = useState<SectorHeatmapMetric>("revenue_yoy_pct");
  const [heatmap, setHeatmap]                       = useState<SectorHeatmap | null>(null);
  const [heatmapLoading, setHeatmapLoading]         = useState(false);

  // Fetch definitions once on mount
  useEffect(() => {
    dataClient
      .getSectorHeatmapDefinitions()
      .then((res) => setHeatmapDefinitions(res.definitions ?? []))
      .catch(() => {});
  }, []);

  // Fetch heatmap whenever the group definition or metric changes
  useEffect(() => {
    let cancelled = false;
    setHeatmapLoading(true);
    dataClient
      .getSectorHeatmap({
        group_definition: heatmapGroupDef,
        quarters:         20,
        metric:           heatmapMetric,
      })
      .then((res) => {
        if (!cancelled) setHeatmap(res);
      })
      .catch(() => {
        if (!cancelled) setHeatmap(null);
      })
      .finally(() => {
        if (!cancelled) setHeatmapLoading(false);
      });
    return () => { cancelled = true; };
  }, [heatmapGroupDef, heatmapMetric]);

  return (
    <DataExplorerView
      loadedTickers={loadedTickers}
      activeTicker={activeTicker}
      rows={activeRows}
      loading={loading}
      error={error}
      apiWarnings={apiWarnings}
      onTickerChange={setActiveTicker}
      onAddTicker={handleAddTicker}
      onRemoveTicker={handleRemoveTicker}
      marginInsights={activeInsights}
      marginInsightsLoading={insightsLoading}
      marginInsightsError={insightsError}
      onRefreshMarginInsights={handleRefreshInsights}
      onEditMarginInsights={handleEditInsights}
      heatmapDefinitions={heatmapDefinitions}
      heatmapGroupDef={heatmapGroupDef}
      heatmapMetric={heatmapMetric}
      heatmap={heatmap}
      heatmapLoading={heatmapLoading}
      onHeatmapGroupDefChange={setHeatmapGroupDef}
      onHeatmapMetricChange={setHeatmapMetric}
    />
  );
}
