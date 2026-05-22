"""Japanese stock data adapter for ai-hedge-fund.

Routes JP tickers (4-digit numeric codes, e.g. '4751') to yfinance and, in later
phases, J-Quants / EDINET. Public functions match the shapes of src/tools/api.py
so src/tools/api.py can delegate transparently.

API key conventions: ALL credentials are read via os.environ.get() only.
Never hardcode. Future env vars (added to .env.example in advance):
  - JQUANTS_REFRESH_TOKEN   (Phase 2.2, J-Quants LITE)
  - EDINET_API_KEY          (Phase 2.3, EDINET XBRL)
"""

import logging
import re

from src.data.models import (
    CompanyNews,
    FinancialMetrics,
    InsiderTrade,
    LineItem,
    Price,
)

logger = logging.getLogger(__name__)

_JP_TICKER_RE = re.compile(r"^\d{4}$")


def is_jp_ticker(ticker: str) -> bool:
    """4-digit numeric ticker = Japanese stock (TSE / Nagoya / Fukuoka / Sapporo)."""
    return bool(_JP_TICKER_RE.match(ticker))


def to_yfinance_symbol(ticker: str) -> str:
    """JP ticker '4751' -> yfinance symbol '4751.T' (Tokyo Stock Exchange)."""
    return f"{ticker}.T"


# ---------------------------------------------------------------------------
# Price data (Phase 2.1, yfinance)
# ---------------------------------------------------------------------------

def get_prices_jp(ticker: str, start_date: str, end_date: str) -> list[Price]:
    """Fetch JP stock daily OHLCV via yfinance."""
    import yfinance as yf

    yf_symbol = to_yfinance_symbol(ticker)
    try:
        df = yf.Ticker(yf_symbol).history(
            start=start_date,
            end=end_date,
            interval="1d",
            auto_adjust=False,
        )
    except Exception as e:
        logger.warning("yfinance prices failed for %s: %s", ticker, e)
        return []

    if df is None or df.empty:
        logger.warning("No yfinance price data for %s (%s ~ %s)", ticker, start_date, end_date)
        return []

    prices: list[Price] = []
    for idx, row in df.iterrows():
        try:
            prices.append(
                Price(
                    open=float(row["Open"]),
                    close=float(row["Close"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    volume=int(row["Volume"]) if row["Volume"] == row["Volume"] else 0,
                    time=idx.strftime("%Y-%m-%dT%H:%M:%S"),
                )
            )
        except (ValueError, TypeError) as e:
            logger.debug("Skip bad row for %s at %s: %s", ticker, idx, e)
            continue

    return prices


def get_market_cap_jp(ticker: str, end_date: str) -> float | None:
    """Fetch JP market cap (current snapshot) via yfinance.

    Note: yfinance .info returns current state, not historical. Phase 2.2 may
    add historical market cap via J-Quants.
    """
    import yfinance as yf

    yf_symbol = to_yfinance_symbol(ticker)
    try:
        info = yf.Ticker(yf_symbol).info
    except Exception as e:
        logger.warning("yfinance market_cap failed for %s: %s", ticker, e)
        return None

    mc = info.get("marketCap")
    if mc is None:
        return None
    try:
        return float(mc)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Financial metrics (Phase 2.1, yfinance-only; multi-period in Phase 2.2)
# ---------------------------------------------------------------------------

def get_financial_metrics_jp(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[FinancialMetrics]:
    """Fetch JP financial metrics via yfinance.

    Returns a single-period snapshot. Multi-period history (limit>1) returns the
    same snapshot duplicated — Phase 2.2 will provide real time-series via J-Quants.
    """
    import yfinance as yf

    yf_symbol = to_yfinance_symbol(ticker)
    try:
        info = yf.Ticker(yf_symbol).info
    except Exception as e:
        logger.warning("yfinance info failed for %s: %s", ticker, e)
        return []

    if not info:
        return []

    def _f(key: str) -> float | None:
        v = info.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # yfinance debtToEquity is in percent (e.g., 50.5 == 50.5%).
    # Buffett agent compares against 0.5, so normalize to ratio.
    d2e_pct = _f("debtToEquity")
    debt_to_equity = d2e_pct / 100.0 if d2e_pct is not None else None

    market_cap = _f("marketCap")
    fcf = _f("freeCashflow")
    fcf_yield = fcf / market_cap if (fcf and market_cap) else None

    shares = _f("sharesOutstanding")
    fcf_per_share = fcf / shares if (fcf and shares) else None

    metric = FinancialMetrics(
        ticker=ticker,
        report_period=end_date,
        period=period,
        currency=info.get("currency", "JPY"),
        market_cap=market_cap,
        enterprise_value=_f("enterpriseValue"),
        price_to_earnings_ratio=_f("trailingPE"),
        price_to_book_ratio=_f("priceToBook"),
        price_to_sales_ratio=_f("priceToSalesTrailing12Months"),
        enterprise_value_to_ebitda_ratio=_f("enterpriseToEbitda"),
        enterprise_value_to_revenue_ratio=_f("enterpriseToRevenue"),
        free_cash_flow_yield=fcf_yield,
        peg_ratio=_f("pegRatio") or _f("trailingPegRatio"),
        gross_margin=_f("grossMargins"),
        operating_margin=_f("operatingMargins"),
        net_margin=_f("profitMargins"),
        return_on_equity=_f("returnOnEquity"),
        return_on_assets=_f("returnOnAssets"),
        return_on_invested_capital=None,
        asset_turnover=None,
        inventory_turnover=None,
        receivables_turnover=None,
        days_sales_outstanding=None,
        operating_cycle=None,
        working_capital_turnover=None,
        current_ratio=_f("currentRatio"),
        quick_ratio=_f("quickRatio"),
        cash_ratio=None,
        operating_cash_flow_ratio=None,
        debt_to_equity=debt_to_equity,
        debt_to_assets=None,
        interest_coverage=None,
        revenue_growth=_f("revenueGrowth"),
        earnings_growth=_f("earningsGrowth"),
        book_value_growth=None,
        earnings_per_share_growth=_f("earningsQuarterlyGrowth"),
        free_cash_flow_growth=None,
        operating_income_growth=None,
        ebitda_growth=None,
        payout_ratio=_f("payoutRatio"),
        earnings_per_share=_f("trailingEps"),
        book_value_per_share=_f("bookValue"),
        free_cash_flow_per_share=fcf_per_share,
    )

    if limit <= 1:
        return [metric]
    # Phase 2.2 will replace this with real time-series. For now, duplicate.
    return [metric] * limit


# ---------------------------------------------------------------------------
# Stubs — implemented in later sub-phases
# ---------------------------------------------------------------------------

def search_line_items_jp(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[LineItem]:
    """Fetch JP line items via EDINET XBRL (latest Securities Report).

    Currently returns the latest annual snapshot only. Multi-period (limit > 1)
    duplicates the snapshot — same convention as get_financial_metrics_jp until
    Phase 2.3.2 (multi-annual fetch) is implemented.

    Returns [] when EDINET fetch fails (e.g., EDINET_API_KEY not set), allowing
    callers to degrade gracefully.
    """
    from src.tools.edinet import get_line_items_for_ticker

    data = get_line_items_for_ticker(ticker, line_items)
    if data is None:
        return []

    extras = {name: data.get(name) for name in line_items}

    try:
        item = LineItem(
            ticker=ticker,
            report_period=data.get("report_period") or end_date,
            period=period,
            currency=data.get("currency") or "JPY",
            **extras,
        )
    except Exception as e:
        logger.warning("Failed to build LineItem for %s: %s", ticker, e)
        return []

    return [item] * max(1, limit)


def get_insider_trades_jp(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[InsiderTrade]:
    """Phase 2.5 will implement via EDINET 役員提出書 / TDnet 大量保有報告書."""
    logger.info(
        "get_insider_trades_jp: not yet implemented (Phase 2.5). Returning empty list for %s.",
        ticker,
    )
    return []


def get_company_news_jp(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[CompanyNews]:
    """Phase 2.4 will implement via TDnet 適時開示 + optionally 株探."""
    logger.info(
        "get_company_news_jp: TDnet integration not yet implemented (Phase 2.4). "
        "Returning empty list for %s.",
        ticker,
    )
    return []
