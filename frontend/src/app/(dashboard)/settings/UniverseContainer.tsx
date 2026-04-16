"use client";

import { useEffect } from "react";
import { useUniverseStore, type Ticker } from "@/store/useUniverseStore";
import UniverseView from "./UniverseView";

export default function UniverseContainer() {
  const {
    tickers,
    sectors,
    addTicker,
    removeTicker,
    addSector,
    removeSector,
    syncFromBackend,
  } = useUniverseStore();

  // Sync build status from backend on mount
  useEffect(() => {
    syncFromBackend();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <UniverseView
      tickers={tickers}
      sectors={sectors}
      onAddTicker={(ticker: Ticker) => addTicker(ticker)}
      onRemoveTicker={(symbol: string) => removeTicker(symbol)}
      onAddSector={(sector: string) => addSector(sector)}
      onRemoveSector={(sector: string) => removeSector(sector)}
    />
  );
}
