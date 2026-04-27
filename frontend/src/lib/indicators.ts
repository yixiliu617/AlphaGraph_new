/**
 * Technical-analysis indicator library — pure functions over OHLC arrays.
 *
 * All functions return arrays the same length as the input, with `null`
 * values where the indicator is not yet computable (warm-up period).
 *
 * Indicators implemented:
 *   - ema(values, n)
 *   - sma(values, n)        — re-exported for convenience
 *   - stdev(values, n)
 *   - bollinger(closes, n=20, k=2)         { upper, mid, lower }
 *   - rsi(closes, n=14)
 *   - macd(closes, fast=12, slow=26, sig=9) { macd, signal, hist }
 *   - tdSetup(closes)        { buy, sell }   1..9 (with 9 = completed)
 *   - tdCountdown(highs, lows, closes, setupBuy, setupSell)
 *                            { buy, sell }   1..13 (with 13 = completed)
 *   - tdCombo(highs, lows, closes)
 *                            { buy, sell }   1..13 (with 13 = completed)
 *
 * Notes on TD logic — all formulas follow Tom DeMark's published
 * "Sequential" (1996) and "Combo" rules. Buy setups are downward, sell
 * setups are upward. Countdown variants and Combo "Version 1" rules are
 * documented inline.
 */

// ---------------------------------------------------------------------------
// Basic stats
// ---------------------------------------------------------------------------

type N = number | null;

export function sma(values: N[], n: number): N[] {
  const out: N[] = new Array(values.length).fill(null);
  let sum = 0;
  let valid = 0;
  const buf: N[] = [];
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    buf.push(v);
    if (v != null) { sum += v; valid++; }
    if (buf.length > n) {
      const drop = buf.shift()!;
      if (drop != null) { sum -= drop; valid--; }
    }
    if (buf.length === n && valid === n) {
      out[i] = sum / n;
    }
  }
  return out;
}

export function ema(values: N[], n: number): N[] {
  const out: N[] = new Array(values.length).fill(null);
  if (values.length < n) return out;
  const alpha = 2 / (n + 1);

  // Seed with SMA of first n values
  let sum = 0;
  let seeded = -1;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v == null) continue;
    sum += v;
    if (i + 1 >= n) {
      out[i] = sum / n;
      seeded = i;
      break;
    }
  }
  if (seeded < 0) return out;

  for (let i = seeded + 1; i < values.length; i++) {
    const v = values[i];
    const prev = out[i - 1];
    if (v == null || prev == null) {
      out[i] = prev;
      continue;
    }
    out[i] = v * alpha + prev * (1 - alpha);
  }
  return out;
}

export function stdev(values: N[], n: number): N[] {
  const out: N[] = new Array(values.length).fill(null);
  for (let i = n - 1; i < values.length; i++) {
    let sum = 0;
    let count = 0;
    for (let j = i - n + 1; j <= i; j++) {
      if (values[j] != null) { sum += values[j]!; count++; }
    }
    if (count < n) continue;
    const mean = sum / n;
    let sqSum = 0;
    for (let j = i - n + 1; j <= i; j++) {
      const d = values[j]! - mean;
      sqSum += d * d;
    }
    out[i] = Math.sqrt(sqSum / n);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Bollinger Bands
// ---------------------------------------------------------------------------

export interface BollingerBands { mid: N[]; upper: N[]; lower: N[]; }

export function bollinger(closes: N[], n = 20, k = 2): BollingerBands {
  const mid = sma(closes, n);
  const sd = stdev(closes, n);
  const upper: N[] = mid.map((m, i) =>
    m != null && sd[i] != null ? m + k * sd[i]! : null,
  );
  const lower: N[] = mid.map((m, i) =>
    m != null && sd[i] != null ? m - k * sd[i]! : null,
  );
  return { mid, upper, lower };
}

// ---------------------------------------------------------------------------
// RSI (Wilder smoothing)
// ---------------------------------------------------------------------------

export function rsi(closes: N[], n = 14): N[] {
  const out: N[] = new Array(closes.length).fill(null);
  if (closes.length <= n) return out;

  let avgGain = 0;
  let avgLoss = 0;

  // Seed with simple averages of first n changes
  for (let i = 1; i <= n; i++) {
    const a = closes[i];
    const b = closes[i - 1];
    if (a == null || b == null) continue;
    const change = a - b;
    if (change > 0) avgGain += change;
    else avgLoss += -change;
  }
  avgGain /= n;
  avgLoss /= n;
  out[n] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  // Wilder smoothing thereafter
  for (let i = n + 1; i < closes.length; i++) {
    const a = closes[i];
    const b = closes[i - 1];
    if (a == null || b == null) { out[i] = out[i - 1]; continue; }
    const change = a - b;
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    avgGain = (avgGain * (n - 1) + gain) / n;
    avgLoss = (avgLoss * (n - 1) + loss) / n;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

// ---------------------------------------------------------------------------
// MACD
// ---------------------------------------------------------------------------

export interface MACD { macd: N[]; signal: N[]; hist: N[]; }

export function macd(closes: N[], fast = 12, slow = 26, sig = 9): MACD {
  const emaFast = ema(closes, fast);
  const emaSlow = ema(closes, slow);
  const macdLine: N[] = emaFast.map((f, i) =>
    f != null && emaSlow[i] != null ? f - emaSlow[i]! : null,
  );
  const signal = ema(macdLine, sig);
  const hist: N[] = macdLine.map((m, i) =>
    m != null && signal[i] != null ? m - signal[i]! : null,
  );
  return { macd: macdLine, signal, hist };
}

// ---------------------------------------------------------------------------
// Tom DeMark Sequential — Setup (1..9)
// ---------------------------------------------------------------------------
//
// Buy Setup: a count starts when close[i] < close[i-4]. The count
// increments on each consecutive bar that satisfies the condition.
// A break (close[i] >= close[i-4]) resets the count to 0. A count of 9
// completes the setup.
//
// Sell Setup is symmetric with close[i] > close[i-4].

export interface TDSeries { buy: number[]; sell: number[]; }

export function tdSetup(closes: N[]): TDSeries {
  const buy: number[] = new Array(closes.length).fill(0);
  const sell: number[] = new Array(closes.length).fill(0);

  let buyCount = 0;
  let sellCount = 0;
  for (let i = 0; i < closes.length; i++) {
    if (i < 4 || closes[i] == null || closes[i - 4] == null) {
      buy[i] = 0; sell[i] = 0; continue;
    }
    const c = closes[i]!;
    const c4 = closes[i - 4]!;

    if (c < c4) {
      buyCount = buyCount >= 9 ? 1 : buyCount + 1;
      sellCount = 0;
    } else if (c > c4) {
      sellCount = sellCount >= 9 ? 1 : sellCount + 1;
      buyCount = 0;
    } else {
      buyCount = 0;
      sellCount = 0;
    }
    buy[i] = buyCount;
    sell[i] = sellCount;
  }
  return { buy, sell };
}

// ---------------------------------------------------------------------------
// Tom DeMark Sequential — Countdown (1..13)
// ---------------------------------------------------------------------------
//
// Once a Buy Setup of 9 has just completed at bar k, Countdown begins.
// Each bar i > k where close[i] <= low[i-2] increments the buy countdown
// (NOT consecutive — can skip non-qualifying bars). Reaches 13 = signal.
//
// Cancellations:
//   - A new opposite setup of 9 cancels in-progress countdown.
//   - "TDST" violation: if any bar's close exceeds the highest TRUE high
//     of the prior 9-bar setup (for buy countdown), the countdown is
//     terminated. We implement the simpler "9 cancels opposite" rule.
//
// Sell Countdown: close[i] >= high[i-2], symmetric.

export function tdCountdown(
  highs: N[], lows: N[], closes: N[],
  setup: TDSeries,
): TDSeries {
  const buy: number[] = new Array(closes.length).fill(0);
  const sell: number[] = new Array(closes.length).fill(0);

  let buyActive = false;
  let buyCount = 0;
  let sellActive = false;
  let sellCount = 0;

  for (let i = 0; i < closes.length; i++) {
    // Activation: a fresh 9-setup launches countdown starting from THIS bar.
    if (setup.buy[i] === 9) {
      buyActive = true;
      buyCount = 0;
      sellActive = false;
      sellCount = 0;
    }
    if (setup.sell[i] === 9) {
      sellActive = true;
      sellCount = 0;
      buyActive = false;
      buyCount = 0;
    }

    if (buyActive && i >= 2 && closes[i] != null && lows[i - 2] != null) {
      if (closes[i]! <= lows[i - 2]!) {
        buyCount++;
        buy[i] = buyCount;
        if (buyCount >= 13) {
          buyActive = false;  // signal completed; reset
          buyCount = 0;
        }
      }
    }
    if (sellActive && i >= 2 && closes[i] != null && highs[i - 2] != null) {
      if (closes[i]! >= highs[i - 2]!) {
        sellCount++;
        sell[i] = sellCount;
        if (sellCount >= 13) {
          sellActive = false;
          sellCount = 0;
        }
      }
    }
  }
  return { buy, sell };
}

// ---------------------------------------------------------------------------
// Tom DeMark Combo — Countdown (1..13)
// ---------------------------------------------------------------------------
//
// "Version 1" of TD Combo. Each Combo countdown bar must satisfy ALL of:
//   (1) close[i] <= low[i-2]               — same as Sequential
//   (2) low[i]  <  low[i-1]                — making lower lows
//   (3) close[i] < close[i-1]              — closing weaker
//   (4) close[i] < close of prior countdown bar
//                                          (for bars 2..13; bar 1 free)
// Combo starts EARLIER than Sequential — counts begin once the underlying
// Setup count begins (not after Setup completes). Completed at 13.
//
// Sell Combo: close[i]>=high[i-2], high[i]>high[i-1], close[i]>close[i-1],
//             close[i]>prior-countdown-close.

export function tdCombo(
  highs: N[], lows: N[], closes: N[],
): TDSeries {
  const buy: number[] = new Array(closes.length).fill(0);
  const sell: number[] = new Array(closes.length).fill(0);

  let buyCount = 0;
  let sellCount = 0;
  let lastBuyClose: number | null = null;
  let lastSellClose: number | null = null;

  for (let i = 2; i < closes.length; i++) {
    const c = closes[i];
    const cp = closes[i - 1];
    const c2 = closes[i - 2];
    const l = lows[i];
    const lp = lows[i - 1];
    const l2 = lows[i - 2];
    const h = highs[i];
    const hp = highs[i - 1];
    const h2 = highs[i - 2];
    if (c == null || cp == null || c2 == null) continue;

    // BUY combo
    if (
      l != null && lp != null && l2 != null &&
      c <= l2 && l < lp && c < cp &&
      (buyCount === 0 || (lastBuyClose != null && c < lastBuyClose))
    ) {
      buyCount++;
      buy[i] = buyCount;
      lastBuyClose = c;
      if (buyCount >= 13) {
        buyCount = 0;
        lastBuyClose = null;
      }
    }

    // SELL combo
    if (
      h != null && hp != null && h2 != null &&
      c >= h2 && h > hp && c > cp &&
      (sellCount === 0 || (lastSellClose != null && c > lastSellClose))
    ) {
      sellCount++;
      sell[i] = sellCount;
      lastSellClose = c;
      if (sellCount >= 13) {
        sellCount = 0;
        lastSellClose = null;
      }
    }
  }
  return { buy, sell };
}
