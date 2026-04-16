"use client";

import { useState } from "react";
import { X, Plus, Globe, CheckCircle2, Loader2, AlertTriangle } from "lucide-react";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import type { Ticker } from "@/store/useUniverseStore";

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface UniverseViewProps {
  tickers: Ticker[];
  sectors: string[];
  onAddTicker: (ticker: Ticker) => void;
  onRemoveTicker: (symbol: string) => void;
  onAddSector: (sector: string) => void;
  onRemoveSector: (sector: string) => void;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SectorChip({
  label,
  onRemove,
}: {
  label: string;
  onRemove: () => void;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700">
      {label}
      <button
        onClick={onRemove}
        aria-label={`Remove ${label}`}
        className="text-slate-400 hover:text-slate-700 transition-colors"
      >
        <X size={12} />
      </button>
    </span>
  );
}

function SectorPanel({
  sectors,
  onAddSector,
  onRemoveSector,
}: {
  sectors: string[];
  onAddSector: (s: string) => void;
  onRemoveSector: (s: string) => void;
}) {
  const [draft, setDraft] = useState("");

  function handleAdd() {
    const trimmed = draft.trim();
    if (!trimmed) return;
    onAddSector(trimmed);
    setDraft("");
  }

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-6 flex flex-col gap-5">
      <div>
        <h2 className="text-sm font-semibold text-slate-900">Sectors</h2>
        <p className="text-xs text-slate-500 mt-0.5">
          Define the sectors that scope your research universe.
        </p>
      </div>

      <div className="flex flex-wrap gap-2 min-h-[40px]">
        {sectors.length === 0 && (
          <p className="text-xs text-slate-400 italic">No sectors defined.</p>
        )}
        {sectors.map((s) => (
          <SectorChip key={s} label={s} onRemove={() => onRemoveSector(s)} />
        ))}
      </div>

      <div className="flex gap-2 pt-2 border-t border-slate-100">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="e.g. AI Infrastructure"
          className="flex-1 border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900 placeholder:text-slate-400"
        />
        <button
          onClick={handleAdd}
          disabled={!draft.trim()}
          className={cn(
            "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
            draft.trim()
              ? "bg-slate-900 text-white hover:bg-slate-700"
              : "bg-slate-100 text-slate-400 cursor-not-allowed"
          )}
        >
          <Plus size={14} />
          Add
        </button>
      </div>
    </div>
  );
}

function BuildStatusBadge({ ticker }: { ticker: Ticker }) {
  if (ticker.staleWarning) {
    return (
      <span
        title={ticker.staleWarning}
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-50 text-amber-700 border border-amber-200"
      >
        <AlertTriangle size={9} />
        Stale
      </span>
    );
  }
  if (ticker.buildStatus === "built" && ticker.lastPeriodEnd) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
        <CheckCircle2 size={9} />
        {ticker.lastPeriodEnd}
      </span>
    );
  }
  if (ticker.buildStatus === "building") {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium bg-slate-50 text-slate-500 border border-slate-200">
        <Loader2 size={9} className="animate-spin" />
        Building…
      </span>
    );
  }
  return (
    <span className="text-[10px] text-slate-300">—</span>
  );
}

function TickerRow({
  ticker,
  onRemove,
}: {
  ticker: Ticker;
  onRemove: () => void;
}) {
  return (
    <tr className="border-b border-slate-100 last:border-0 hover:bg-slate-50 group">
      <td className="py-3 pr-4">
        <span className="font-mono text-sm font-semibold text-slate-900 tracking-wide">
          {ticker.symbol}
        </span>
      </td>
      <td className="py-3 pr-4">
        <span className="text-sm text-slate-700">{ticker.name}</span>
      </td>
      <td className="py-3 pr-4">
        <span className="inline-block px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-600">
          {ticker.sector}
        </span>
      </td>
      <td className="py-3 pr-4">
        <BuildStatusBadge ticker={ticker} />
      </td>
      <td className="py-3 text-right">
        <button
          onClick={onRemove}
          aria-label={`Remove ${ticker.symbol}`}
          className="text-slate-300 hover:text-red-500 transition-colors opacity-0 group-hover:opacity-100"
        >
          <X size={14} />
        </button>
      </td>
    </tr>
  );
}

const EMPTY_DRAFT = { symbol: "", name: "", sector: "" };

function TickerPanel({
  tickers,
  sectors,
  onAddTicker,
  onRemoveTicker,
}: {
  tickers: Ticker[];
  sectors: string[];
  onAddTicker: (t: Ticker) => void;
  onRemoveTicker: (symbol: string) => void;
}) {
  const [draft, setDraft] = useState(EMPTY_DRAFT);
  const [error, setError] = useState("");

  function handleAdd() {
    const symbol = draft.symbol.trim().toUpperCase();
    const name = draft.name.trim();
    const sector = draft.sector.trim();

    if (!symbol) { setError("Ticker symbol is required."); return; }
    if (!name)   { setError("Company name is required."); return; }
    if (!sector) { setError("Sector is required."); return; }

    if (tickers.some((t) => t.symbol === symbol)) {
      setError(`${symbol} is already in your universe.`);
      return;
    }

    onAddTicker({ symbol, name, sector });
    setDraft(EMPTY_DRAFT);
    setError("");
  }

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-6 flex flex-col gap-5">
      <div>
        <h2 className="text-sm font-semibold text-slate-900">Tickers</h2>
        <p className="text-xs text-slate-500 mt-0.5">
          The individual companies and instruments you actively track.
        </p>
      </div>

      <div className="overflow-auto max-h-80">
        {tickers.length === 0 ? (
          <p className="text-xs text-slate-400 italic py-4 text-center">
            No tickers in your universe yet.
          </p>
        ) : (
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-slate-200">
                <th className="pb-2 pr-4 text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                  Symbol
                </th>
                <th className="pb-2 pr-4 text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                  Company
                </th>
                <th className="pb-2 pr-4 text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                  Sector
                </th>
                <th className="pb-2 pr-4 text-[10px] font-semibold text-slate-500 uppercase tracking-wider">
                  Data Status
                </th>
                <th className="pb-2 text-[10px] font-semibold text-slate-500 uppercase tracking-wider" />
              </tr>
            </thead>
            <tbody>
              {tickers.map((t) => (
                <TickerRow
                  key={t.symbol}
                  ticker={t}
                  onRemove={() => onRemoveTicker(t.symbol)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="pt-2 border-t border-slate-100 flex flex-col gap-2">
        <div className="flex gap-2">
          <input
            type="text"
            value={draft.symbol}
            onChange={(e) => { setDraft((d) => ({ ...d, symbol: e.target.value })); setError(""); }}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            placeholder="Symbol"
            className="w-24 border border-slate-200 rounded-lg px-3 py-1.5 text-sm font-mono uppercase placeholder:normal-case placeholder:font-sans focus:outline-none focus:ring-2 focus:ring-slate-900 placeholder:text-slate-400"
          />
          <input
            type="text"
            value={draft.name}
            onChange={(e) => { setDraft((d) => ({ ...d, name: e.target.value })); setError(""); }}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            placeholder="Company name"
            className="flex-1 border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900 placeholder:text-slate-400"
          />
          <select
            value={draft.sector}
            onChange={(e) => { setDraft((d) => ({ ...d, sector: e.target.value })); setError(""); }}
            className="w-44 border border-slate-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-slate-900 text-slate-700 bg-white"
          >
            <option value="">Select sector</option>
            {sectors.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button
            onClick={handleAdd}
            disabled={!draft.symbol.trim() || !draft.name.trim() || !draft.sector}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors shrink-0",
              draft.symbol.trim() && draft.name.trim() && draft.sector
                ? "bg-slate-900 text-white hover:bg-slate-700"
                : "bg-slate-100 text-slate-400 cursor-not-allowed"
            )}
          >
            <Plus size={14} />
            Add
          </button>
        </div>

        {error && (
          <p className="text-xs text-red-500">{error}</p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main view
// ---------------------------------------------------------------------------

export default function UniverseView({
  tickers,
  sectors,
  onAddTicker,
  onRemoveTicker,
  onAddSector,
  onRemoveSector,
}: UniverseViewProps) {
  return (
    <div className="flex flex-col gap-6 h-full p-6 overflow-y-auto">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-slate-100 flex items-center justify-center">
            <Globe size={18} className="text-slate-600" />
          </div>
          <div>
            <h1 className="text-lg font-bold text-slate-900">Coverage Universe</h1>
            <p className="text-xs text-slate-500">
              {tickers.length} ticker{tickers.length !== 1 ? "s" : ""} ·{" "}
              {sectors.length} sector{sectors.length !== 1 ? "s" : ""}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-1.5 text-xs text-slate-400">
          <CheckCircle2 size={13} className="text-emerald-400" />
          Changes saved automatically
        </div>
      </div>

      {/* Note */}
      <div className="bg-slate-50 border border-slate-200 rounded-lg px-4 py-3 text-xs text-slate-600 leading-relaxed">
        Your coverage universe drives the <span className="font-medium">Mission Control</span> feed,
        cold-start data seeding, and the entity suggestions in the{" "}
        <span className="font-medium">Unified Engine</span>. Keep it focused on what you actively
        track.
      </div>

      {/* Panels */}
      <div className="grid grid-cols-1 xl:grid-cols-[300px_1fr] gap-6">
        <SectorPanel
          sectors={sectors}
          onAddSector={onAddSector}
          onRemoveSector={onRemoveSector}
        />
        <TickerPanel
          tickers={tickers}
          sectors={sectors}
          onAddTicker={onAddTicker}
          onRemoveTicker={onRemoveTicker}
        />
      </div>
    </div>
  );
}
