"""
Data API router — serves financial metrics from SEC parquet files.

POST /data/fetch                  -- fetch metrics for given tickers/period
GET  /data/metrics                -- list all available metric names
GET  /data/calculated/status      -- show last calculated layer build report
POST /data/calculated/build       -- (re)build calculated layer for given tickers
GET  /data/topline/status         -- filing state, build report, stale warnings
POST /data/topline/refresh        -- incremental EDGAR refresh (background)
POST /data/topline/add-ticker     -- add ticker to universe + full first-time build
"""

import json
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from backend.app.services.data_agent.calculator import CalculatedLayerBuilder
from backend.app.services.data_agent.data_agent import DataAgent, DataSpec, DataResult
from backend.app.services.data_agent.concept_map import (
    ALL_METRICS,
    BASE_METRIC_CONCEPTS,
    COMPUTED_METRICS,
    METRIC_META,
    TEMPORAL_METRICS,
)
from backend.app.services.data_agent.topline_builder import ToplineBuilder

router = APIRouter(prefix="/data", tags=["data"])

_agent            = DataAgent()
_builder          = CalculatedLayerBuilder()
_topline_builder  = ToplineBuilder()


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

@router.post("/fetch", response_model=DataResult)
async def fetch_data(spec: DataSpec) -> DataResult:
    """
    Fetch financial metrics for the requested tickers and period.

    Routes to the pre-computed calculated layer when available (faster, includes
    YoY/QoQ growth). Falls back to raw SEC parquet with on-the-fly computation.
    """
    if not spec.tickers:
        raise HTTPException(status_code=422, detail="tickers must not be empty")
    if not spec.metrics:
        raise HTTPException(status_code=422, detail="metrics must not be empty")
    return _agent.fetch(spec)


# ---------------------------------------------------------------------------
# Metric catalogue
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Cell-level drill-down: "where did this number come from?"
# ---------------------------------------------------------------------------

# Which parquet each metric is stored in (topline layer)
_INCOME_METRICS = {
    "revenue", "cost_of_revenue", "gross_profit", "rd_expense", "sga_expense",
    "total_opex", "opex", "operating_income", "interest_expense", "interest_income",
    "other_income_net", "pretax_income", "income_tax", "net_income",
    "eps_basic", "eps_diluted", "shares_basic", "shares_diluted",
}
_CASHFLOW_METRICS = {
    "operating_cf", "investing_cf", "financing_cf", "capex", "depreciation",
    "free_cash_flow",
}


@router.get("/cell-source")
async def cell_source(ticker: str, metric: str, end_date: str) -> dict[str, Any]:
    """
    Return the underlying source information for a single table cell so the
    frontend can render a drill-down modal (Where did this number come from?).

    Response shape:
      {
        ticker, metric, metric_label, value, unit,
        period_end, period_start, fiscal_period, fiscal_quarter, fiscal_year,
        is_ytd, source_layer, source_file,
        xbrl_concepts: [...],        # base metrics only
        derivation: { formula, inputs } | None,  # computed metrics only
        filing: { form, accession, filed_date, edgar_url } | None,
      }
    """
    import pandas as pd
    from pathlib import Path

    ticker = ticker.upper().strip()
    if not _topline_builder.is_available(ticker):
        raise HTTPException(status_code=404, detail=f"No topline data for {ticker}")

    # ── Pick the right statement file based on the metric ───────────────
    if metric in _CASHFLOW_METRICS:
        statement = "cash_flow"
    else:
        # Default to income statement. Balance-sheet metrics aren't exposed
        # in the current table, so we don't route to it.
        statement = "income_statement"

    # ── Read the parquet row matching end_date ─────────────────────────
    try:
        df = _topline_builder.read(ticker, statement, lookback_years=15.0)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    target = pd.Timestamp(end_date).normalize()
    close = df[(df["period_end"] - target).abs() <= pd.Timedelta(days=3)]
    if close.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No row near {end_date} for {ticker} in {statement}",
        )
    row = close.iloc[0]

    # ── Build derivation info for computed metrics ─────────────────────
    meta = METRIC_META.get(metric, {})
    derivation: dict | None = None
    xbrl_concepts: list[str] = []
    value: Any = row[metric] if metric in row.index else None

    if metric in COMPUTED_METRICS:
        spec = COMPUTED_METRICS[metric]
        inputs = {dep: (float(row[dep]) if dep in row.index and row[dep] == row[dep] else None)
                  for dep in spec["requires"]}
        # Try to replay the formula for transparency
        try:
            if all(v is not None for v in inputs.values()):
                value = spec["formula"](inputs)
            else:
                value = None
        except Exception:
            pass
        derivation = {
            "formula_description": _format_formula(metric),
            "inputs": inputs,
        }
    elif metric in TEMPORAL_METRICS:
        base = TEMPORAL_METRICS[metric]["base_metric"]
        if metric.endswith("_diff_yoy"):
            formula_desc = (
                f"YoY percentage-point delta of {METRIC_META.get(base, {}).get('label', base)}: "
                f"current margin - same margin 4 quarters ago (absolute pp difference)"
            )
        else:
            suffix = "YoY" if metric.endswith("_yoy_pct") else "QoQ"
            formula_desc = (
                f"{suffix} growth of {METRIC_META.get(base, {}).get('label', base)}: "
                f"(current - {'4 quarters ago' if suffix == 'YoY' else 'prior quarter'}) "
                f"/ |prior| * 100"
            )
        derivation = {
            "formula_description": formula_desc,
            "inputs": {"base_metric": base},
        }
    elif metric in BASE_METRIC_CONCEPTS:
        xbrl_concepts = list(BASE_METRIC_CONCEPTS[metric])

    # ── Filing info from _filing_state.json (if it exists) ─────────────
    filing_info = _get_filing_info(ticker)

    # ── Fiscal period label ────────────────────────────────────────────
    fy = int(row["fiscal_year"]) if "fiscal_year" in row.index and row["fiscal_year"] == row["fiscal_year"] else None
    fq = str(row["fiscal_quarter"]) if "fiscal_quarter" in row.index else None
    fiscal_period = f"FY{fy}-{fq}" if fy and fq else None

    source_file = f"backend/data/filing_data/topline/{statement}/ticker={ticker}.parquet"

    return {
        "ticker":          ticker,
        "metric":          metric,
        "metric_label":    meta.get("label", metric),
        "value":           float(value) if value is not None and value == value else None,
        "unit":            meta.get("unit", "M"),
        "period_end":      row["period_end"].strftime("%Y-%m-%d") if "period_end" in row.index else None,
        "period_start":    row["period_start"].strftime("%Y-%m-%d") if "period_start" in row.index and row["period_start"] is not None else None,
        "fiscal_period":   fiscal_period,
        "fiscal_quarter":  fq,
        "fiscal_year":     fy,
        "is_ytd":          bool(row["is_ytd"]) if "is_ytd" in row.index else False,
        "source_layer":    "topline",
        "source_file":     source_file,
        "xbrl_concepts":   xbrl_concepts,
        "derivation":      derivation,
        "filing":          filing_info,
    }


def _format_formula(metric: str) -> str:
    """Human-readable formula for a computed metric."""
    formulas = {
        "gross_margin_pct":     "gross_profit / revenue * 100",
        "operating_margin_pct": "operating_income / revenue * 100",
        "net_margin_pct":       "net_income / revenue * 100",
        "free_cash_flow":       "operating_cf + capex  (capex is negative)",
        "rd_pct_revenue":       "rd_expense / revenue * 100",
        "opex":                 "gross_profit - operating_income",
    }
    return formulas.get(metric, metric)


def _get_filing_info(ticker: str) -> dict | None:
    """
    Return latest 10-Q / 10-K accession + EDGAR URL for this ticker.
    Returns None if filing state is not available.
    """
    import json
    from pathlib import Path

    state_file = Path(__file__).resolve().parents[4] / "data" / "filing_data" / "topline" / "_filing_state.json"
    if not state_file.exists():
        return None
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        return None

    entry = state.get(ticker) or state.get(ticker.upper())
    if not entry:
        return None

    # Prefer 10-Q, fall back to 10-K
    accession = entry.get("10q_accession") or entry.get("10k_accession")
    form      = "10-Q" if entry.get("10q_accession") else ("10-K" if entry.get("10k_accession") else None)
    if not accession:
        return None

    # Build a generic EDGAR company filings URL — always works even without
    # a precise filing document URL (which would require another EDGAR call).
    edgar_url = (
        f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
        f"&CIK={ticker}&type={form}&dateb=&owner=include&count=40"
    )

    return {
        "form":       form,
        "accession":  accession,
        "filed_date": entry.get("last_built_at"),
        "edgar_url":  edgar_url,
    }


@router.get("/metrics")
async def list_metrics() -> dict:
    """Return all available metric names, grouped by type."""
    from backend.app.services.data_agent.concept_map import (
        BASE_METRIC_CONCEPTS, COMPUTED_METRICS, TEMPORAL_METRICS
    )
    return {
        "base":     sorted(BASE_METRIC_CONCEPTS.keys()),
        "computed": sorted(COMPUTED_METRICS.keys()),
        "temporal": sorted(TEMPORAL_METRICS.keys()),
        "all":      sorted(ALL_METRICS),
    }


# ---------------------------------------------------------------------------
# Calculated layer management
# ---------------------------------------------------------------------------

class BuildRequest(BaseModel):
    tickers: list[str] | None = None  # None = all available tickers


@router.get("/calculated/status")
async def calculated_status() -> dict[str, Any]:
    """Return the last calculated layer build report."""
    return _builder.status()


@router.get("/quality-report")
async def quality_report(ticker: str | None = None) -> dict[str, Any]:
    """
    Return the data quality report(s) from the last calculated-layer build.

    Each per-ticker report contains:
      - rows_checked, rules_evaluated, rules_skipped
      - passed (bool) — true when no unsuppressed FAIL-severity violations
      - fail_count, warn_count
      - violations[] with {rule, severity, end_date, message, suppressed}

    Violations suppressed by data_quality/exceptions.py still appear in the
    list (marked `suppressed=True`) so the UI can surface them for audit.
    """
    status = _builder.status()
    if not status.get("built"):
        raise HTTPException(status_code=404, detail="Calculated layer has not been built yet")

    all_reports = {
        t: entry.get("validation", {}).get("quality_report")
        for t, entry in status.get("tickers", {}).items()
        if entry.get("validation", {}).get("quality_report")
    }

    if ticker:
        t = ticker.upper().strip()
        if t not in all_reports:
            raise HTTPException(status_code=404, detail=f"No quality report for {t}")
        return {t: all_reports[t]}

    return all_reports


@router.post("/calculated/build")
async def build_calculated(req: BuildRequest, background_tasks: BackgroundTasks) -> dict:
    """
    (Re)build the calculated layer in the background.
    Safe to run at any time — writes to a separate directory from raw backbone.
    """
    def _run():
        _builder.build(tickers=req.tickers)

    background_tasks.add_task(_run)
    return {
        "status": "building",
        "tickers": req.tickers or "all",
        "message": "Build started in background. Check GET /data/calculated/status for progress.",
    }


# ---------------------------------------------------------------------------
# Topline management
# ---------------------------------------------------------------------------

class ToplineRefreshRequest(BaseModel):
    tickers: list[str] | None = None  # None = full universe
    force: bool = False               # rebuild even if no new filing detected


class AddTickerRequest(BaseModel):
    ticker: str


@router.get("/topline/status")
async def topline_status() -> dict[str, Any]:
    """
    Return the topline build report, per-ticker filing state (accession numbers,
    last period end, last build timestamp), and any stale warnings.

    A ticker is flagged stale when its last known period_end is >50 days ago
    and no new filing has been detected yet.
    """
    return _topline_builder.status()


@router.post("/topline/refresh")
async def topline_refresh(
    req: ToplineRefreshRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Trigger an incremental topline refresh in the background.

    Polls EDGAR for new 10-K / 10-Q / 10-K/A / 10-Q/A filings and rebuilds
    only the tickers whose accession number has changed (or all if force=True).
    Amendment-driven updates are flagged with is_amendment_update=True in the
    filing state.

    Check GET /data/topline/status for results after the background task finishes.
    """
    def _run() -> None:
        _topline_builder.refresh(tickers=req.tickers, force=req.force)

    background_tasks.add_task(_run)
    return {
        "status":  "refreshing",
        "tickers": req.tickers or "universe",
        "force":   req.force,
        "message": (
            "Refresh started in background. "
            "Check GET /data/topline/status for progress."
        ),
    }


# ---------------------------------------------------------------------------
# Sector heatmap — revenue YoY % matrix grouped by ticker_groups.json
# ---------------------------------------------------------------------------

_CONFIG_DIR       = Path(__file__).resolve().parents[4] / "data" / "config"
_TICKER_GROUPS_FP = _CONFIG_DIR / "ticker_groups.json"
_CALC_DIR         = Path(__file__).resolve().parents[4] / "data" / "filing_data" / "calculated"


def _load_ticker_groups() -> dict:
    if not _TICKER_GROUPS_FP.exists():
        return {"group_definitions": {}}
    try:
        return json.loads(_TICKER_GROUPS_FP.read_text(encoding="utf-8"))
    except Exception:
        return {"group_definitions": {}}


@router.get("/sector-heatmap/definitions")
def sector_heatmap_definitions() -> dict:
    """
    List available group definitions for the sector heatmap. Each entry is:
        {key, label, group_names: [...]}
    """
    cfg = _load_ticker_groups()
    defs = cfg.get("group_definitions", {})
    out: list[dict] = []
    for key, spec in defs.items():
        out.append({
            "key":         key,
            "label":       spec.get("label", key),
            "group_names": list((spec.get("groups") or {}).keys()),
        })
    return {"definitions": out}


_HEATMAP_METRICS: dict[str, dict] = {
    "revenue_yoy_pct":    {"col": "revenue_yoy_pct",    "label": "Revenue YoY %", "fmt": "%"},
    "revenue_qoq_pct":    {"col": "revenue_qoq_pct",    "label": "Revenue QoQ %", "fmt": "%"},
    "revenue":            {"col": "revenue",            "label": "Revenue ($M)",  "fmt": "$M"},
    "net_income":         {"col": "net_income",         "label": "Net Income ($M)", "fmt": "$M"},
    "net_income_yoy_pct": {"col": "net_income_yoy_pct", "label": "Net Income YoY %", "fmt": "%"},
    "net_income_qoq_pct": {"col": "net_income_qoq_pct", "label": "Net Income QoQ %", "fmt": "%"},
}


@router.get("/sector-heatmap")
def sector_heatmap(
    group_definition: str = Query("GICS_industry"),
    quarters:         int = Query(20, ge=2, le=24),
    metric:           str = Query("revenue_yoy_pct"),
) -> dict:
    """
    Return a per-ticker sequence of the selected metric for the last N
    quarters, grouped by the selected group_definition.

    Supported metrics: revenue, revenue_yoy_pct, revenue_qoq_pct,
    net_income, net_income_yoy_pct, net_income_qoq_pct.

    Approach:
      - Read the calculated parquet for each ticker as-is (no rebuild).
      - Sort rows by end_date descending; take the top N quarterly rows.
      - Use edgartools' fiscal_year + fiscal_quarter from ONLY the latest
        row as the anchor. All prior rows are labeled by stepping backward
        one fiscal quarter at a time (Q4 -> Q3, Q1 -> prior year Q4).
      - If the stepped-back label doesn't match what edgartools labeled
        for that row, emit a `mismatches` entry so the frontend can show
        a small footnote below the table. The stepped-back label is the
        source of truth for display.

    Column alignment across tickers is RELATIVE, not absolute — column 0
    is each ticker's most recent reported quarter, column 1 is one
    quarter prior, etc. Column headers in the frontend should read
    "LATEST / −1Q / −2Q / ..." rather than fiscal or calendar labels.

    Response shape:
        {
          group_definition, label,
          quarters_count: N,
          groups: [
            {
              name: "Semiconductors",
              rows: [
                {
                  ticker: "NVDA",
                  latest_label: "FY2026-Q4",
                  latest_end_date: "2026-01-25",
                  points: [
                    {label, end_date, yoy, edgar_label, matches},
                    ...
                  ],
                  mismatches: [{position, expected, edgar}],
                }
              ]
            }
          ]
        }
    """
    cfg = _load_ticker_groups()
    defs = cfg.get("group_definitions", {})
    spec = defs.get(group_definition)
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown group_definition '{group_definition}'. "
                f"Available: {list(defs.keys())}"
            ),
        )

    metric_spec = _HEATMAP_METRICS.get(metric)
    if metric_spec is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown metric '{metric}'. "
                f"Available: {list(_HEATMAP_METRICS.keys())}"
            ),
        )
    metric_col = metric_spec["col"]

    groups_cfg: dict = spec.get("groups", {}) or {}

    def _step_back(fy: int, q_num: int, steps: int) -> tuple[int, int]:
        """Step back `steps` fiscal quarters from (fy, q_num). Returns
        (new_fy, new_q_num) with q_num in 1..4."""
        total = fy * 4 + (q_num - 1) - steps
        new_fy = total // 4
        new_q = (total % 4) + 1
        return new_fy, new_q

    def _fmt_label(fy: int, q_num: int) -> str:
        return f"FY{fy}-Q{q_num}"

    def _build_row(ticker: str) -> dict | None:
        path = _CALC_DIR / f"ticker={ticker}.parquet"
        if not path.exists():
            return {
                "ticker":          ticker,
                "latest_label":    None,
                "latest_end_date": None,
                "points":          [],
                "mismatches":      [],
            }
        try:
            df = pd.read_parquet(
                path,
                columns=[
                    "end_date", "fiscal_year", "fiscal_quarter",
                    "is_ytd", metric_col,
                ],
            )
        except Exception:
            return None

        df = df[
            df["fiscal_quarter"].isin(["Q1", "Q2", "Q3", "Q4"])
            & (~df["is_ytd"].astype(bool))
            & df["end_date"].notna()
        ].copy()
        df["end_date"] = pd.to_datetime(df["end_date"])
        df = df.sort_values("end_date", ascending=False).head(quarters)

        if df.empty:
            return {
                "ticker":          ticker,
                "latest_label":    None,
                "latest_end_date": None,
                "points":          [],
                "mismatches":      [],
            }

        # Anchor: the latest row's edgartools fy + q. This is the ONLY
        # label we trust directly. All other rows are derived from it.
        latest = df.iloc[0]
        try:
            anchor_fy = int(latest["fiscal_year"])
        except Exception:
            anchor_fy = None
        anchor_q_str = str(latest["fiscal_quarter"])
        try:
            anchor_q = int(anchor_q_str.replace("Q", ""))
        except Exception:
            anchor_q = None

        points: list[dict] = []
        mismatches: list[dict] = []

        for pos, (_, row) in enumerate(df.iterrows()):
            end_date_str = row["end_date"].strftime("%Y-%m-%d")
            raw_val = row.get(metric_col)
            value = float(raw_val) if pd.notna(raw_val) else None

            # EDGAR label for this row (for mismatch reporting)
            edgar_label = None
            try:
                if pd.notna(row["fiscal_year"]):
                    edgar_label = f"FY{int(row['fiscal_year'])}-{row['fiscal_quarter']}"
            except Exception:
                edgar_label = None

            # Computed (stepped-back) label from the anchor
            computed_label = None
            if anchor_fy is not None and anchor_q is not None:
                step_fy, step_q = _step_back(anchor_fy, anchor_q, pos)
                computed_label = _fmt_label(step_fy, step_q)

            matches = (computed_label is not None and edgar_label == computed_label)

            points.append({
                "label":       computed_label,
                "end_date":    end_date_str,
                "value":       value,
                # back-compat alias so older frontends still see "yoy"
                "yoy":         value,
                "edgar_label": edgar_label,
                "matches":     matches,
            })

            if computed_label is not None and edgar_label is not None and not matches:
                mismatches.append({
                    "position": pos,
                    "end_date": end_date_str,
                    "expected": computed_label,
                    "edgar":    edgar_label,
                })

        return {
            "ticker":          ticker,
            "latest_label":    points[0]["label"] if points else None,
            "latest_end_date": points[0]["end_date"] if points else None,
            "points":          points,
            "mismatches":      mismatches,
        }

    # Cache per-ticker row in case the same ticker appears in multiple groups
    ticker_cache: dict[str, dict] = {}
    for ticker_list in groups_cfg.values():
        for t in ticker_list:
            if t not in ticker_cache:
                row = _build_row(t)
                if row is not None:
                    ticker_cache[t] = row

    groups_out: list[dict] = []
    for group_name, ticker_list in groups_cfg.items():
        rows = [ticker_cache[t] for t in ticker_list if t in ticker_cache]
        groups_out.append({"name": group_name, "rows": rows})

    return {
        "group_definition": group_definition,
        "label":            spec.get("label", group_definition),
        "quarters_count":   quarters,
        "metric":           metric,
        "metric_label":     metric_spec["label"],
        "metric_fmt":       metric_spec["fmt"],   # "%" or "$M"
        "groups":           groups_out,
    }


@router.post("/topline/add-ticker")
async def topline_add_ticker(
    req: AddTickerRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Add a new ticker to the universe and trigger a full first-time build in the
    background.

    Steps (all run asynchronously):
      1. Register ticker in universe.json
      2. Pull full EDGAR history and build topline parquets
      3. Seed filing state with current accession numbers
      4. Build the calculated layer for this ticker

    Check GET /data/topline/status for completion.
    """
    ticker = req.ticker.upper().strip()

    def _run() -> None:
        _topline_builder.add_ticker(ticker)

    background_tasks.add_task(_run)
    return {
        "status":  "building",
        "ticker":  ticker,
        "message": (
            f"Full first-time build started for {ticker} in background. "
            f"Check GET /data/topline/status for progress."
        ),
    }
