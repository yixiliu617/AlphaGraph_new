"""
Universe service package.

Owns:
  - Fetchers for ETF/index baselines (SPY, SMH, TWSE 50, Nikkei 225, KOSPI 200).
  - Auto-promotion logic when a user adds a ticker not in the broad universe.
  - The effective-universe query helper used by Pillar A features.
"""
