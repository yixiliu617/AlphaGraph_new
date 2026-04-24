"use client";

import type { ScraperHealth } from "@/lib/api/taiwanClient";

interface Props {
  health: ScraperHealth[];
}

export default function TaiwanHealthIndicator({ health }: Props) {
  const anyFailed = health.some((s) => s.status === "failed");
  const anyDegraded = health.some((s) => s.status === "degraded");
  const color = anyFailed ? "bg-red-500" : anyDegraded ? "bg-amber-500" : "bg-green-500";
  const label = anyFailed ? "Scraper failed" : anyDegraded ? "Scraper degraded" : "All scrapers ok";
  const tip = health.length === 0
    ? "No scraper heartbeats yet (first run pending)."
    : health.map((s) => `${s.scraper_name}: ${s.status}${s.lag_seconds != null ? ` (lag ${s.lag_seconds}s)` : ""}`).join("\n");

  return (
    <div className="flex items-center gap-2" title={tip}>
      <span className={`w-2.5 h-2.5 rounded-full ${color} animate-pulse`} />
      <span className="text-xs text-slate-500">{label}</span>
    </div>
  );
}
