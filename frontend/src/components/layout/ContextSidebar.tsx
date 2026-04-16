"use client";

/**
 * ContextSidebar — collapsible left panel for research context.
 *
 * NOT a navigation component. Shows:
 *   - Company universe grouped by sector (from useUniverseStore)
 *   - Connected data sources (static labels)
 *   - Link to Universe settings
 *
 * Expands on hover: 56px → 224px with smooth transition.
 */

import { useState } from "react";
import Link from "next/link";
import {
  Building2, Layers, ChevronDown, ChevronRight,
  Database, FileText, FileBarChart, Newspaper, Settings,
} from "lucide-react";
import { useUniverseStore } from "@/store/useUniverseStore";

const SOURCES = [
  { name: "Broker Reports", icon: FileText },
  { name: "SEC Filings",    icon: FileBarChart },
  { name: "News",           icon: Newspaper },
];

function cn(...classes: (string | false | undefined)[]) {
  return classes.filter(Boolean).join(" ");
}

export default function ContextSidebar() {
  const [expanded, setExpanded]           = useState(false);
  const [universeOpen, setUniverseOpen]   = useState(true);
  const [sourcesOpen, setSourcesOpen]     = useState(false);
  const { tickers, sectors }              = useUniverseStore();

  // Group tickers by sector, skip empty sectors
  const bySector = sectors
    .map((s) => ({ sector: s, items: tickers.filter((t) => t.sector === s) }))
    .filter((g) => g.items.length > 0);

  return (
    <aside
      className={cn(
        "h-full bg-white border-r border-slate-200 flex flex-col shrink-0 z-20",
        "transition-[width] duration-250 ease-in-out overflow-hidden",
        expanded ? "w-56" : "w-14",
      )}
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => setExpanded(false)}
    >
      {/* Panel label */}
      <div className="h-11 flex items-center px-4 border-b border-slate-100 shrink-0 gap-3">
        <Database size={16} className="text-slate-400 shrink-0" />
        <span className={cn(
          "text-[11px] font-semibold text-slate-500 uppercase tracking-wider whitespace-nowrap",
          "transition-opacity duration-200",
          expanded ? "opacity-100" : "opacity-0",
        )}>
          Research Context
        </span>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden">

        {/* ── Company Universe ── */}
        <div className="pt-2">
          <button
            onClick={() => setUniverseOpen((v) => !v)}
            title={expanded ? undefined : "Company Universe"}
            className="w-full flex items-center gap-3 px-4 py-2 hover:bg-slate-50 transition-colors text-left"
          >
            <Building2 size={15} className="text-indigo-500 shrink-0" />
            <span className={cn(
              "flex-1 flex items-center justify-between text-xs font-semibold text-slate-700 whitespace-nowrap",
              "transition-opacity duration-200",
              expanded ? "opacity-100" : "opacity-0",
            )}>
              Universe
              {universeOpen
                ? <ChevronDown size={12} className="text-slate-400" />
                : <ChevronRight size={12} className="text-slate-400" />}
            </span>
          </button>

          {expanded && universeOpen && (
            <div className="pb-2">
              {bySector.length === 0 ? (
                <p className="px-4 py-2 text-[11px] text-slate-400 italic">
                  No tickers — add in Universe settings.
                </p>
              ) : (
                bySector.map(({ sector, items }) => (
                  <div key={sector}>
                    {/* Sector label */}
                    <div className="px-4 pt-2 pb-0.5">
                      <span className="text-[9px] font-bold text-slate-400 uppercase tracking-wider truncate block">
                        {sector}
                      </span>
                    </div>
                    {/* Tickers */}
                    {items.map((ticker) => (
                      <div
                        key={ticker.symbol}
                        className="flex items-center gap-2 pl-5 pr-4 py-1 hover:bg-slate-50 cursor-default group"
                      >
                        <span className="shrink-0 px-1.5 py-0.5 text-[10px] font-mono font-bold bg-indigo-50 text-indigo-700 rounded border border-indigo-100">
                          {ticker.symbol}
                        </span>
                        <span className="text-[11px] text-slate-500 truncate leading-tight">
                          {ticker.name}
                        </span>
                      </div>
                    ))}
                  </div>
                ))
              )}
            </div>
          )}
        </div>

        {/* ── Data Sources ── */}
        <div className="border-t border-slate-100 pt-2">
          <button
            onClick={() => setSourcesOpen((v) => !v)}
            title={expanded ? undefined : "Data Sources"}
            className="w-full flex items-center gap-3 px-4 py-2 hover:bg-slate-50 transition-colors text-left"
          >
            <Layers size={15} className="text-slate-400 shrink-0" />
            <span className={cn(
              "flex-1 flex items-center justify-between text-xs font-semibold text-slate-700 whitespace-nowrap",
              "transition-opacity duration-200",
              expanded ? "opacity-100" : "opacity-0",
            )}>
              Data Sources
              {sourcesOpen
                ? <ChevronDown size={12} className="text-slate-400" />
                : <ChevronRight size={12} className="text-slate-400" />}
            </span>
          </button>

          {expanded && sourcesOpen && (
            <div className="pb-2">
              {SOURCES.map(({ name, icon: Icon }) => (
                <div
                  key={name}
                  className="flex items-center gap-2.5 pl-5 pr-4 py-1.5 hover:bg-slate-50 cursor-default"
                >
                  <Icon size={13} className="text-slate-400 shrink-0" />
                  <span className="text-[11px] text-slate-600 truncate">{name}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Footer — settings link */}
      <div className="border-t border-slate-100 shrink-0">
        <Link
          href="/settings"
          title={expanded ? undefined : "Universe Settings"}
          className="flex items-center gap-3 px-4 py-3 hover:bg-slate-50 transition-colors"
        >
          <Settings size={15} className="text-slate-400 shrink-0" />
          <span className={cn(
            "text-[11px] text-slate-500 whitespace-nowrap transition-opacity duration-200",
            expanded ? "opacity-100" : "opacity-0",
          )}>
            Universe Settings
          </span>
        </Link>
      </div>
    </aside>
  );
}
