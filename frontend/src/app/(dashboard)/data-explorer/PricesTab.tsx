"use client";

/**
 * PricesTab — reusable equity-prices view, ticker-parameterised.
 *
 * Plugs into any company panel (TSMC, UMC, MediaTek, future tickers) by
 * passing the Yahoo-format ticker. The same component scales to the
 * 2000-ticker universe — there's nothing here that needs to be cloned
 * per company.
 *
 * Layout, top to bottom:
 *   1. Key-stats card (last close, change, 52w range, 1Y return, ADV)
 *   2. Interval toggle: Daily | 15m
 *   3. Price chart (filled-area close) + volume bars
 *
 * Data source: /api/v1/prices/{ticker}/{daily|intraday|stats}.
 */

import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import {
  Area, ComposedChart, Bar, BarChart, CartesianGrid, Cell, Line,
  ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis,
} from "recharts";

import {
  pricesClient,
  type PriceBar,
  type PriceSeries,
  type PriceStats,
} from "@/lib/api/pricesClient";

import {
  bollinger as calcBollinger,
  ema as calcEma,
  macd as calcMacd,
  rsi as calcRsi,
  tdCombo as calcTdCombo,
  tdCountdown as calcTdCountdown,
  tdSetup as calcTdSetup,
} from "@/lib/indicators";


type Interval = "daily" | "intraday";

interface IndicatorToggles {
  sma: boolean;            // SMA 50/100/200 already shown by default
  ema: boolean;            // EMA 9/21/50
  bollinger: boolean;      // BB(20, 2σ)
  rsi: boolean;            // RSI(14) sub-pane
  macd: boolean;           // MACD(12,26,9) sub-pane
  tdSequential: boolean;   // TD Setup (9) + Countdown (13) markers
  tdCombo: boolean;        // TD Combo (13) markers
}

const DEFAULT_TOGGLES: IndicatorToggles = {
  sma: true,
  ema: false,
  bollinger: true,
  rsi: true,
  macd: true,
  tdSequential: true,
  tdCombo: false,
};


interface Props {
  ticker: string;
  /** Currency label for tooltips and stats card. Defaults to "USD" for non-.TW; "TWD" for .TW. */
  currency?: string;
  /** Default daily window in days. */
  defaultDays?: number;
  /** Default intraday window in bars. */
  defaultIntradayBars?: number;
}


// (sma is now in @/lib/indicators)
import { sma as calcSma } from "@/lib/indicators";


export default function PricesTab({
  ticker,
  currency,
  defaultDays = 1825,
  defaultIntradayBars = 200,
}: Props) {
  const [interval, setInterval] = useState<Interval>("daily");
  const [days, setDays] = useState(defaultDays);
  const [bars, setBars] = useState(defaultIntradayBars);
  const [yScale, setYScale] = useState<"linear" | "log">(defaultDays >= 1825 ? "log" : "linear");
  const [tog, setTog] = useState<IndicatorToggles>(DEFAULT_TOGGLES);

  const [series, setSeries] = useState<PriceSeries | null>(null);
  const [stats, setStats] = useState<PriceStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const ccy = currency ?? (ticker.endsWith(".TW") ? "TWD" : "USD");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    const seriesP = interval === "daily"
      ? pricesClient.daily(ticker, days)
      : pricesClient.intraday(ticker, bars, "15m");
    Promise.all([seriesP, pricesClient.stats(ticker)])
      .then(([s, st]) => {
        if (cancelled) return;
        setSeries(s);
        setStats(st);
      })
      .catch((e) => {
        if (cancelled) return;
        setErr(e instanceof Error ? e.message : String(e));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ticker, interval, days, bars]);

  const chartRows = useMemo(() => {
    if (!series) return [];
    const isDaily = interval === "daily";
    const closes = series.rows.map((r) => r.c);
    const highs = series.rows.map((r) => r.h);
    const lows = series.rows.map((r) => r.l);

    const sma50 = isDaily && tog.sma ? calcSma(closes, 50) : [];
    const sma100 = isDaily && tog.sma ? calcSma(closes, 100) : [];
    const sma200 = isDaily && tog.sma ? calcSma(closes, 200) : [];
    const ema9 = isDaily && tog.ema ? calcEma(closes, 9) : [];
    const ema21 = isDaily && tog.ema ? calcEma(closes, 21) : [];
    const ema50 = isDaily && tog.ema ? calcEma(closes, 50) : [];
    const bb = isDaily && tog.bollinger
      ? calcBollinger(closes, 20, 2)
      : { mid: [] as (number | null)[], upper: [] as (number | null)[], lower: [] as (number | null)[] };
    const rsiArr = isDaily && tog.rsi ? calcRsi(closes, 14) : [];
    const macdRes = isDaily && tog.macd
      ? calcMacd(closes, 12, 26, 9)
      : { macd: [] as (number | null)[], signal: [] as (number | null)[], hist: [] as (number | null)[] };
    const tdSetup = isDaily && (tog.tdSequential || tog.tdCombo) ? calcTdSetup(closes) : { buy: [], sell: [] };
    const tdCount = isDaily && tog.tdSequential ? calcTdCountdown(highs, lows, closes, tdSetup) : { buy: [], sell: [] };
    const tdCmb = isDaily && tog.tdCombo ? calcTdCombo(highs, lows, closes) : { buy: [], sell: [] };

    // Successful-only display masks. The user wants count numbers (and the
    // 9 / 13 pointers) to appear ONLY when a setup / countdown actually
    // completes — never during an in-progress run that might fail. We do
    // this retroactively: walk the count series, and when a 9 is found,
    // backfill the prior 8 bars with display values 1-8. When countdown
    // reaches its 4th qualifying bar (== "13" in the user's continuous
    // numbering), backfill the prior 3 qualifying bars with 10/11/12.
    const showBuySetup: number[] = new Array(closes.length).fill(0);
    const showSellSetup: number[] = new Array(closes.length).fill(0);
    const showBuyCount: number[] = new Array(closes.length).fill(0);
    const showSellCount: number[] = new Array(closes.length).fill(0);
    if (isDaily && tog.tdSequential) {
      for (let i = 0; i < closes.length; i++) {
        if (tdSetup.buy[i] === 9) {
          for (let k = 0; k < 9; k++) showBuySetup[i - 8 + k] = k + 1;
        }
        if (tdSetup.sell[i] === 9) {
          for (let k = 0; k < 9; k++) showSellSetup[i - 8 + k] = k + 1;
        }
        if (tdCount.buy[i] === 4) {
          showBuyCount[i] = 13;
          let tgt = 3;
          let j = i - 1;
          while (tgt > 0 && j >= 0) {
            if (tdCount.buy[j] === tgt) { showBuyCount[j] = 9 + tgt; tgt--; }
            j--;
          }
        }
        if (tdCount.sell[i] === 4) {
          showSellCount[i] = 13;
          let tgt = 3;
          let j = i - 1;
          while (tgt > 0 && j >= 0) {
            if (tdCount.sell[j] === tgt) { showSellCount[j] = 9 + tgt; tgt--; }
            j--;
          }
        }
      }
    }

    return series.rows.map((r: PriceBar, i: number) => {
      // Successful-only display: setup numbers 1-9 only appear if the
      // setup reached 9; countdown numbers 10-13 only appear if the
      // countdown reached its 4th qualifying bar (=user-visible "13").
      // In-progress runs that never complete display nothing.
      const tdBuyValue: number | null = (showBuySetup[i] || showBuyCount[i]) || null;
      const tdSellValue: number | null = (showSellSetup[i] || showSellCount[i]) || null;

      const buyComboHit = isDaily && tog.tdCombo && tdCmb.buy[i] === 13;
      const sellComboHit = isDaily && tog.tdCombo && tdCmb.sell[i] === 13;

      return {
        t: r.t,
        tShort: shortTime(r.t, interval),
        close: r.c,
        high: r.h,
        low: r.l,
        volume: r.v,
        sma50: sma50[i] ?? null,
        sma100: sma100[i] ?? null,
        sma200: sma200[i] ?? null,
        ema9: ema9[i] ?? null,
        ema21: ema21[i] ?? null,
        ema50: ema50[i] ?? null,
        bbUpper: bb.upper[i] ?? null,
        bbMid: bb.mid[i] ?? null,
        bbLower: bb.lower[i] ?? null,
        rsi: rsiArr[i] ?? null,
        macdLine: macdRes.macd[i] ?? null,
        macdSignal: macdRes.signal[i] ?? null,
        macdHist: macdRes.hist[i] ?? null,
        // TD Sequential — single carrier per side. Y anchored to bar low
        // (buy, drawn below) or bar high (sell, drawn above). The shape
        // function reads tdBuyValue / tdSellValue from payload to decide
        // text vs small-arrow vs big-arrow rendering.
        tdBuyAnchor: tdBuyValue != null ? r.l : null,
        tdBuyValue,
        tdSellAnchor: tdSellValue != null ? r.h : null,
        tdSellValue,
        // TD Combo — completion-only markers (kept simple).
        tdBuyCombo13: buyComboHit ? r.l : null,
        tdSellCombo13: sellComboHit ? r.h : null,
      };
    });
  }, [series, interval, tog]);

  return (
    <div className="space-y-4">
      {/* Stats card */}
      <StatsCard stats={stats} ticker={ticker} ccy={ccy} />

      {/* Controls */}
      <div className="flex items-center gap-2 text-xs">
        <div className="flex rounded-md border border-slate-300 overflow-hidden">
          <button
            type="button"
            onClick={() => setInterval("daily")}
            className={`px-3 py-1 ${interval === "daily" ? "bg-indigo-600 text-white" : "bg-white text-slate-700 hover:bg-slate-50"}`}
          >
            Daily
          </button>
          <button
            type="button"
            onClick={() => setInterval("intraday")}
            className={`px-3 py-1 border-l border-slate-300 ${interval === "intraday" ? "bg-indigo-600 text-white" : "bg-white text-slate-700 hover:bg-slate-50"}`}
          >
            15m intraday
          </button>
        </div>

        {interval === "daily" && (
          <div className="flex rounded-md border border-slate-300 overflow-hidden">
            {[30, 90, 180, 365, 730, 1825, 3650].map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                className={`px-2 py-1 ${days === d ? "bg-slate-700 text-white" : "bg-white text-slate-600 hover:bg-slate-50"} ${d !== 30 ? "border-l border-slate-300" : ""}`}
              >
                {fmtSpan(d)}
              </button>
            ))}
          </div>
        )}
        {interval === "intraday" && (
          <div className="flex rounded-md border border-slate-300 overflow-hidden">
            {[40, 100, 200, 500, 1000].map((b) => (
              <button
                key={b}
                type="button"
                onClick={() => setBars(b)}
                className={`px-2 py-1 ${bars === b ? "bg-slate-700 text-white" : "bg-white text-slate-600 hover:bg-slate-50"} ${b !== 40 ? "border-l border-slate-300" : ""}`}
              >
                {b} bars
              </button>
            ))}
          </div>
        )}

        {/* Linear / log y-axis toggle (essential for multi-year views with splits + big drawdowns) */}
        <div className="flex rounded-md border border-slate-300 overflow-hidden">
          <button
            type="button"
            onClick={() => setYScale("linear")}
            className={`px-2 py-1 ${yScale === "linear" ? "bg-slate-700 text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}
          >
            Linear
          </button>
          <button
            type="button"
            onClick={() => setYScale("log")}
            className={`px-2 py-1 border-l border-slate-300 ${yScale === "log" ? "bg-slate-700 text-white" : "bg-white text-slate-600 hover:bg-slate-50"}`}
          >
            Log
          </button>
        </div>
      </div>

      {/* Indicator toggle chips — only meaningful on daily */}
      {interval === "daily" && (
        <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
          <span className="text-[10px] uppercase tracking-wide text-slate-500 font-semibold mr-1">Indicators:</span>
          <Chip on={tog.sma}          onClick={() => setTog((s) => ({ ...s, sma: !s.sma }))}>SMA 50/100/200</Chip>
          <Chip on={tog.ema}          onClick={() => setTog((s) => ({ ...s, ema: !s.ema }))}>EMA 9/21/50</Chip>
          <Chip on={tog.bollinger}    onClick={() => setTog((s) => ({ ...s, bollinger: !s.bollinger }))}>Bollinger 20/2σ</Chip>
          <Chip on={tog.rsi}          onClick={() => setTog((s) => ({ ...s, rsi: !s.rsi }))}>RSI 14</Chip>
          <Chip on={tog.macd}         onClick={() => setTog((s) => ({ ...s, macd: !s.macd }))}>MACD 12/26/9</Chip>
          <Chip on={tog.tdSequential} onClick={() => setTog((s) => ({ ...s, tdSequential: !s.tdSequential }))}>TD Sequential</Chip>
          <Chip on={tog.tdCombo}      onClick={() => setTog((s) => ({ ...s, tdCombo: !s.tdCombo }))}>TD Combo</Chip>
        </div>
      )}

      {/* Chart */}
      {loading && (
        <div className="flex items-center justify-center h-72 bg-slate-50 rounded-md">
          <Loader2 className="animate-spin text-slate-400" />
        </div>
      )}
      {err && (
        <div className="p-4 bg-rose-50 border border-rose-200 rounded text-rose-800 text-xs">
          Failed to load prices: {err}
        </div>
      )}
      {!loading && !err && series && series.rows.length === 0 && (
        <div className="p-4 bg-slate-50 border border-slate-200 rounded text-slate-600 text-xs">
          No price data for {ticker} in the selected window.
        </div>
      )}
      {!loading && !err && series && series.rows.length > 0 && (
        <>
          <div className="h-80 bg-white border border-slate-200 rounded-md p-2">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartRows} margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
                <defs>
                  <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#4f46e5" stopOpacity={0.30} />
                    <stop offset="100%" stopColor="#4f46e5" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" />
                <XAxis dataKey="tShort" tick={{ fontSize: 10 }} minTickGap={48} stroke="#94a3b8" />
                <YAxis
                  tick={{ fontSize: 10 }}
                  // Add ~10% headroom above and ~5% below so TD Sequential
                  // markers (drawn 12 px outside the bar) and other overlays
                  // don't get clipped when price is at an all-time high.
                  domain={[
                    (dMin: number) => (dMin > 0 ? dMin * 0.95 : dMin),
                    (dMax: number) => (dMax > 0 ? dMax * 1.1 : dMax),
                  ]}
                  stroke="#94a3b8"
                  tickFormatter={(v) => fmtPrice(v, ccy)}
                  width={70}
                  scale={yScale}
                  allowDataOverflow={false}
                />
                <Tooltip
                  contentStyle={{ fontSize: "11px", borderRadius: "4px" }}
                  formatter={(v: number, name: string) => [fmtPrice(v, ccy), name]}
                  labelFormatter={(label, payload) => {
                    const p = payload?.[0]?.payload;
                    return p ? p.t : String(label);
                  }}
                />
                <Area
                  type="monotone"
                  dataKey="close"
                  name="Close"
                  stroke="#4f46e5"
                  strokeWidth={1.5}
                  fill="url(#priceFill)"
                  isAnimationActive={false}
                  dot={false}
                />
                {/* SMA overlays. Rendered as direct children (not inside a Fragment)
                    because Recharts' ComposedChart only inspects direct children
                    when assigning them to series; fragments hide them. */}
                {interval === "daily" && (
                  <Line
                    type="monotone"
                    dataKey="sma50"
                    name="SMA 50"
                    stroke="#0ea5e9"
                    strokeWidth={1.5}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls
                  />
                )}
                {interval === "daily" && (
                  <Line
                    type="monotone"
                    dataKey="sma100"
                    name="SMA 100"
                    stroke="#f59e0b"
                    strokeWidth={1.5}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls
                  />
                )}
                {interval === "daily" && tog.sma && (
                  <Line
                    type="monotone"
                    dataKey="sma200"
                    name="SMA 200"
                    stroke="#dc2626"
                    strokeWidth={1.8}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls
                  />
                )}
                {/* EMA overlays */}
                {interval === "daily" && tog.ema && (
                  <Line type="monotone" dataKey="ema9"  name="EMA 9"  stroke="#10b981" strokeWidth={1.2} strokeDasharray="2 2" dot={false} isAnimationActive={false} connectNulls />
                )}
                {interval === "daily" && tog.ema && (
                  <Line type="monotone" dataKey="ema21" name="EMA 21" stroke="#059669" strokeWidth={1.4} strokeDasharray="2 2" dot={false} isAnimationActive={false} connectNulls />
                )}
                {interval === "daily" && tog.ema && (
                  <Line type="monotone" dataKey="ema50" name="EMA 50" stroke="#047857" strokeWidth={1.6} strokeDasharray="2 2" dot={false} isAnimationActive={false} connectNulls />
                )}
                {/* Bollinger Bands */}
                {interval === "daily" && tog.bollinger && (
                  <Line type="monotone" dataKey="bbUpper" name="BB Upper" stroke="#9333ea" strokeWidth={1.0} dot={false} isAnimationActive={false} connectNulls />
                )}
                {interval === "daily" && tog.bollinger && (
                  <Line type="monotone" dataKey="bbMid"   name="BB Mid"   stroke="#a855f7" strokeWidth={1.0} strokeDasharray="3 3" dot={false} isAnimationActive={false} connectNulls />
                )}
                {interval === "daily" && tog.bollinger && (
                  <Line type="monotone" dataKey="bbLower" name="BB Lower" stroke="#9333ea" strokeWidth={1.0} dot={false} isAnimationActive={false} connectNulls />
                )}
                {/* TD Sequential — single carrier per side. The shape
                    function reads `payload.tdBuyValue` / `payload.tdSellValue`
                    and renders:
                      * small text for counts 1-8, 10-12
                      * SMALL triangle pointer at 9 (setup completion)
                      * BIG triangle pointer at 13 (countdown completion)
                    Buy markers sit BELOW bar (low anchor), pointing UP.
                    Sell markers sit ABOVE bar (high anchor), pointing DOWN. */}
                {interval === "daily" && tog.tdSequential && (
                  <Scatter dataKey="tdBuyAnchor" name="TD Buy" shape={(p: any) => <TDSeqMark {...p} side="buy" />} />
                )}
                {interval === "daily" && tog.tdSequential && (
                  <Scatter dataKey="tdSellAnchor" name="TD Sell" shape={(p: any) => <TDSeqMark {...p} side="sell" />} />
                )}
                {/* TD Combo countdown-13 markers */}
                {interval === "daily" && tog.tdCombo && (
                  <Scatter dataKey="tdBuyCombo13" name="TD Combo Buy 13" shape={(p: any) => <TDMarker {...p} label="C13" color="#0d9488" anchor="below" filled />} />
                )}
                {interval === "daily" && tog.tdCombo && (
                  <Scatter dataKey="tdSellCombo13" name="TD Combo Sell 13" shape={(p: any) => <TDMarker {...p} label="C13" color="#be123c" anchor="above" filled />} />
                )}
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          {interval === "daily" && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-slate-600">
              <LegendDot color="#4f46e5" label="Close" />
              {tog.sma && <LegendDot color="#0ea5e9" label="SMA 50" />}
              {tog.sma && <LegendDot color="#f59e0b" label="SMA 100" />}
              {tog.sma && <LegendDot color="#dc2626" label="SMA 200" />}
              {tog.ema && <LegendDot color="#10b981" label="EMA 9" />}
              {tog.ema && <LegendDot color="#059669" label="EMA 21" />}
              {tog.ema && <LegendDot color="#047857" label="EMA 50" />}
              {tog.bollinger && <LegendDot color="#9333ea" label="Bollinger 20/2σ" />}
              {tog.tdSequential && <LegendDot color="#16a34a" label="TD Buy 1-9 setup → 10-13 countdown (▲ at 9, ▲▲ at 13)" />}
              {tog.tdSequential && <LegendDot color="#dc2626" label="TD Sell 1-9 setup → 10-13 countdown (▼ at 9, ▼▼ at 13)" />}
              {tog.tdCombo && <LegendDot color="#0d9488" label="TD Combo Buy 13" />}
              {tog.tdCombo && <LegendDot color="#be123c" label="TD Combo Sell 13" />}
            </div>
          )}

          {/* RSI sub-pane */}
          {interval === "daily" && tog.rsi && (
            <div className="h-32 bg-white border border-slate-200 rounded-md p-2">
              <div className="text-[10px] text-slate-500 uppercase tracking-wide font-semibold mb-1">RSI 14</div>
              <ResponsiveContainer width="100%" height="85%">
                <ComposedChart data={chartRows} margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
                  <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" />
                  <XAxis dataKey="tShort" tick={{ fontSize: 10 }} minTickGap={48} stroke="#94a3b8" />
                  <YAxis tick={{ fontSize: 10 }} domain={[0, 100]} stroke="#94a3b8" width={30} />
                  <Tooltip contentStyle={{ fontSize: "11px", borderRadius: "4px" }} formatter={(v: number) => v?.toFixed(1)} />
                  <ReferenceLine y={70} stroke="#dc2626" strokeDasharray="3 3" />
                  <ReferenceLine y={30} stroke="#16a34a" strokeDasharray="3 3" />
                  <ReferenceLine y={50} stroke="#94a3b8" strokeDasharray="2 2" />
                  <Line type="monotone" dataKey="rsi" name="RSI" stroke="#7c3aed" strokeWidth={1.4} dot={false} isAnimationActive={false} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* MACD sub-pane */}
          {interval === "daily" && tog.macd && (
            <div className="h-36 bg-white border border-slate-200 rounded-md p-2">
              <div className="text-[10px] text-slate-500 uppercase tracking-wide font-semibold mb-1">MACD 12/26/9</div>
              <ResponsiveContainer width="100%" height="85%">
                <ComposedChart data={chartRows} margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
                  <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" />
                  <XAxis dataKey="tShort" tick={{ fontSize: 10 }} minTickGap={48} stroke="#94a3b8" />
                  <YAxis tick={{ fontSize: 10 }} stroke="#94a3b8" width={50} />
                  <Tooltip contentStyle={{ fontSize: "11px", borderRadius: "4px" }} formatter={(v: number) => v?.toFixed(2)} />
                  <ReferenceLine y={0} stroke="#94a3b8" />
                  <Bar dataKey="macdHist" name="Hist" isAnimationActive={false}>
                    {chartRows.map((row, i) => (
                      <Cell key={`mh-${i}`} fill={(row.macdHist ?? 0) >= 0 ? "#16a34a" : "#dc2626"} />
                    ))}
                  </Bar>
                  <Line type="monotone" dataKey="macdLine"   name="MACD"   stroke="#2563eb" strokeWidth={1.3} dot={false} isAnimationActive={false} connectNulls />
                  <Line type="monotone" dataKey="macdSignal" name="Signal" stroke="#f97316" strokeWidth={1.3} dot={false} isAnimationActive={false} connectNulls />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Volume bars */}
          <div className="h-24 bg-white border border-slate-200 rounded-md p-2">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartRows} margin={{ top: 4, right: 12, left: 8, bottom: 0 }}>
                <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" />
                <XAxis dataKey="tShort" tick={{ fontSize: 10 }} minTickGap={48} stroke="#94a3b8" />
                <YAxis
                  tick={{ fontSize: 10 }}
                  stroke="#94a3b8"
                  tickFormatter={fmtVol}
                  width={70}
                />
                <Tooltip
                  contentStyle={{ fontSize: "11px", borderRadius: "4px" }}
                  formatter={(v: number) => fmtVol(v)}
                  labelFormatter={(_, payload) => payload?.[0]?.payload?.t ?? ""}
                />
                <Bar dataKey="volume" fill="#94a3b8" isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="text-[10px] text-slate-500">
            Source: Yahoo Finance via yfinance.{" "}
            {interval === "intraday" ? "15-minute bars (~15-min delayed)." : "Daily OHLCV, raw + adjusted close."}
          </div>
        </>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Stats card
// ---------------------------------------------------------------------------

function StatsCard({ stats, ticker, ccy }: { stats: PriceStats | null; ticker: string; ccy: string }) {
  if (!stats) {
    return (
      <div className="bg-slate-50 border border-slate-200 rounded-md p-3 h-20 animate-pulse" />
    );
  }
  const upDay = (stats.change_pct ?? 0) >= 0;
  const up1y = (stats.one_year_return_pct ?? 0) >= 0;
  return (
    <div className="bg-white border border-slate-200 rounded-md p-3 grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
      <Stat label="Last close" value={fmtPrice(stats.last_close, ccy)} sub={stats.as_of?.split("T")[0]} />
      <Stat
        label="vs prior session"
        value={stats.change_pct != null ? `${upDay ? "+" : ""}${stats.change_pct.toFixed(2)}%` : "—"}
        valueClass={upDay ? "text-emerald-700" : "text-rose-700"}
        sub={stats.prev_close != null ? `prev ${fmtPrice(stats.prev_close, ccy)}` : ""}
      />
      <Stat
        label="52w range"
        value={`${fmtPrice(stats.low_52w, ccy)} – ${fmtPrice(stats.high_52w, ccy)}`}
      />
      <Stat
        label="1Y return"
        value={stats.one_year_return_pct != null ? `${up1y ? "+" : ""}${stats.one_year_return_pct.toFixed(1)}%` : "—"}
        valueClass={up1y ? "text-emerald-700" : "text-rose-700"}
        sub="adj close basis"
      />
      <Stat
        label="ADV (20d)"
        value={stats.avg_dollar_volume_20d != null ? fmtMoneyShort(stats.avg_dollar_volume_20d, ccy) : "—"}
        sub={`${ticker}`}
      />
    </div>
  );
}

function Stat({
  label, value, sub, valueClass,
}: {
  label: string; value: string; sub?: string; valueClass?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-slate-500 font-semibold">{label}</div>
      <div className={`text-base font-bold mt-0.5 ${valueClass ?? "text-slate-900"}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="inline-block w-3 h-0.5" style={{ backgroundColor: color }} />
      <span>{label}</span>
    </span>
  );
}

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "px-2.5 py-1 rounded-full border text-[11px] transition-colors " +
        (on
          ? "bg-indigo-600 border-indigo-600 text-white shadow-sm"
          : "bg-white border-slate-300 text-slate-600 hover:bg-slate-50")
      }
    >
      {children}
    </button>
  );
}

/**
 * TDSeqMark — Bloomberg-style TD Sequential bar marker.
 *
 * Recharts hands us {cx, cy, payload}. We pull the count value from
 * `payload.tdBuyValue` (or `tdSellValue`), then render one of three things:
 *
 *   - Plain count number (1-8, 10-12): small text 14 px outside the bar
 *   - Setup 9 completion: SMALL triangle pointer outside the bar +
 *     a "9" label. Buy points UP (below the bar); sell points DOWN
 *     (above the bar).
 *   - Countdown 13 completion: BIG triangle pointer + "13" label.
 *     Same direction rules; ~2× the size of the 9-marker.
 */
function TDSeqMark(props: any) {
  const { cx, cy, side, payload } = props;
  if (cx == null || cy == null || !payload) return null;
  const value: number | null = side === "buy" ? payload.tdBuyValue : payload.tdSellValue;
  if (value == null || value < 1) return null;
  const isBuy = side === "buy";
  const color = isBuy ? "#16a34a" : "#dc2626";

  // Offset from the bar (buy = below, sell = above).
  const offsetText = isBuy ? 10 : -10;
  const offsetSmallTri = isBuy ? 8 : -8;
  const offsetBigTri = isBuy ? 12 : -12;

  // Plain count number (1-8, 10-12)
  if (value !== 9 && value !== 13) {
    return (
      <text
        x={cx}
        y={cy + offsetText}
        fontSize={9}
        textAnchor="middle"
        dominantBaseline="middle"
        fill={color}
        fontWeight={600}
      >
        {value}
      </text>
    );
  }

  // Triangle pointer + label (small for 9, big for 13).
  const big = value === 13;
  const triHalf = big ? 7 : 4;          // half-width / half-height of triangle
  const triCy = cy + (isBuy ? offsetBigTri : offsetBigTri);
  // Buy: triangle points UP (apex at top, base at bottom)
  // Sell: triangle points DOWN (apex at bottom, base at top)
  const apex = isBuy
    ? { x: cx, y: triCy - triHalf }
    : { x: cx, y: triCy + triHalf };
  const baseL = isBuy
    ? { x: cx - triHalf, y: triCy + triHalf }
    : { x: cx - triHalf, y: triCy - triHalf };
  const baseR = isBuy
    ? { x: cx + triHalf, y: triCy + triHalf }
    : { x: cx + triHalf, y: triCy - triHalf };

  // Label sits just past the triangle (further from the bar).
  const labelGap = big ? 9 : 7;
  const labelY = isBuy ? triCy + triHalf + labelGap : triCy - triHalf - labelGap;

  return (
    <g>
      <polygon
        points={`${apex.x},${apex.y} ${baseL.x},${baseL.y} ${baseR.x},${baseR.y}`}
        fill={color}
        stroke={color}
        strokeWidth={big ? 1.4 : 1}
      />
      <text
        x={cx}
        y={labelY}
        fontSize={big ? 11 : 9}
        textAnchor="middle"
        dominantBaseline={isBuy ? "hanging" : "auto"}
        fill={color}
        fontWeight={700}
      >
        {value}
      </text>
    </g>
  );
}

/**
 * TDMarker — legacy badge used by TD Combo's completion markers.
 * Kept for the Combo 13 tags (small filled rounded rect with a label).
 */
function TDMarker(props: {
  cx?: number; cy?: number;
  label: string; color: string;
  anchor: "above" | "below";
  filled?: boolean;
}) {
  const { cx, cy, label, color, anchor, filled } = props;
  if (cx == null || cy == null) return null;
  const offset = anchor === "above" ? -14 : 14;
  const w = label.length * 6 + 8;
  const h = 12;
  const x = cx - w / 2;
  const y = cy + offset - h / 2;
  return (
    <g>
      <rect
        x={x} y={y} width={w} height={h} rx={2} ry={2}
        fill={filled ? color : "white"}
        stroke={color}
        strokeWidth={1}
      />
      <text
        x={cx} y={y + h - 3}
        fontSize={9}
        textAnchor="middle"
        fill={filled ? "white" : color}
        fontWeight={600}
      >
        {label}
      </text>
    </g>
  );
}


// ---------------------------------------------------------------------------
// Formatters
// ---------------------------------------------------------------------------

function fmtPrice(v: number | null | undefined, ccy: string): string {
  if (v == null || Number.isNaN(v)) return "—";
  const sym = ccy === "USD" ? "$" : ccy === "TWD" ? "NT$" : "";
  // Intl requires minimumFractionDigits <= maximumFractionDigits.
  // For TWD or any large absolute value, drop both to 0 (whole-NT$ display);
  // otherwise show 2 decimals (e.g. AAPL $202.13).
  const big = Math.abs(v) >= 1000;
  const min = big ? 0 : 2;
  const max = big ? 0 : 2;
  return `${sym}${v.toLocaleString(undefined, { minimumFractionDigits: min, maximumFractionDigits: max })}`;
}

function fmtVol(v: number): string {
  if (v >= 1_000_000_000) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1_000_000) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1e3).toFixed(0)}K`;
  return String(v);
}

function fmtMoneyShort(v: number, ccy: string): string {
  const sym = ccy === "USD" ? "$" : ccy === "TWD" ? "NT$" : "";
  if (v >= 1e12) return `${sym}${(v / 1e12).toFixed(1)}T`;
  if (v >= 1e9) return `${sym}${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${sym}${(v / 1e6).toFixed(1)}M`;
  return `${sym}${v.toFixed(0)}`;
}

function fmtSpan(days: number): string {
  if (days <= 90) return `${days}d`;
  if (days <= 365) return `${Math.round(days / 30)}m`;
  return `${(days / 365).toFixed(0)}y`;
}

function shortTime(iso: string, interval: Interval): string {
  if (!iso) return "";
  // Daily: "MMM 'YY" or "MM-DD" depending on density. Intraday: "MM-DD HH:mm".
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  if (interval === "daily") {
    return `${(d.getUTCMonth() + 1).toString().padStart(2, "0")}-${d.getUTCDate().toString().padStart(2, "0")}-${(d.getUTCFullYear() % 100).toString().padStart(2, "0")}`;
  }
  return `${(d.getUTCMonth() + 1).toString().padStart(2, "0")}-${d.getUTCDate().toString().padStart(2, "0")} ${d.getUTCHours().toString().padStart(2, "0")}:${d.getUTCMinutes().toString().padStart(2, "0")}`;
}
