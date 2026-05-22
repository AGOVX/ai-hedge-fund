"""EDINET XBRL adapter for Japanese line-item extraction.

Wraps `edinet-tools` (matthelmer) to provide Buffett's 12 line items from the
latest Securities Report (有価証券報告書, doc_type='120') of a given JP ticker.

Reads EDINET_API_KEY via os.environ.get() only (never hardcoded). When the
key is absent the module degrades to a warning + empty result so the rest of
the pipeline keeps running.

Mapping of Buffett line items to edinet-tools SecuritiesReport fields:

    capital_expenditure                  -> raw_fields (CapitalExpendituresIA)
    depreciation_and_amortization        -> depreciation_amortization
    net_income                           -> net_income
    outstanding_shares                   -> raw_fields (NumberOfIssuedSharesAtTheEndOfPeriod*)
    total_assets                         -> total_assets
    total_liabilities                    -> total_liabilities
    shareholders_equity                  -> net_assets   (JP equivalent)
    dividends_and_other_cash_distributions -> raw_fields (CashDividendsPaid)
    issuance_or_purchase_of_equity_shares  -> raw_fields (PurchaseOfTreasuryShares)
    gross_profit                         -> raw_fields (GrossProfit)
    revenue                              -> net_sales   (JP equivalent)
    free_cash_flow                       -> operating_cash_flow - investing_cash_flow (computed)
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache

logger = logging.getLogger(__name__)

# Top-level imports of edinet_tools are deferred to inside functions so that
# environments missing the package (or missing the API key) still allow the
# rest of the codebase to import this module without crashing.


_XBRL_ALIASES: dict[str, list[str]] = {
    # Buffett's name -> candidate XBRL element-name fragments (case-insensitive substr match)
    "capital_expenditure": [
        "CapitalExpendituresIA",
        "PurchaseOfPropertyPlantAndEquipment",
        "CapitalExpenditures",
    ],
    "outstanding_shares": [
        "NumberOfIssuedSharesAtTheEndOfPeriodCommonStock",
        "NumberOfIssuedSharesAtTheEndOfPeriod",
        "TotalNumberOfIssuedShares",
    ],
    "dividends_and_other_cash_distributions": [
        "CashDividendsPaid",
        "DividendsPaid",
        "TotalDividends",
    ],
    "issuance_or_purchase_of_equity_shares": [
        "PurchaseOfTreasuryShares",
        "PurchaseOfTreasuryStock",
        "ProceedsFromIssuanceOfCommonStock",
        "ProceedsFromSaleOfTreasuryShares",
    ],
    "gross_profit": [
        "GrossProfit",
        "GrossProfits",
    ],
}


def _has_api_key() -> bool:
    """True only when an EDINET_API_KEY is set to something other than the placeholder."""
    k = os.environ.get("EDINET_API_KEY", "")
    return bool(k) and k != "your-edinet-api-key"


@lru_cache(maxsize=128)
def _fetch_latest_securities_report(ticker: str, max_age_days: int = 540):
    """Fetch the latest Securities Report (有報, doc_type=120) for a ticker.

    Returns a parsed SecuritiesReport or None on any failure (missing key,
    no filings, parse error). Results are cached in-process per ticker.
    """
    if not _has_api_key():
        logger.warning(
            "EDINET_API_KEY not set — search_line_items_jp will return []. "
            "Register at https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1 "
            "and paste the Subscription Key into .env."
        )
        return None

    try:
        import edinet_tools
    except ImportError as e:
        logger.error("edinet-tools not installed: %s", e)
        return None

    try:
        entity = edinet_tools.entity(ticker)
    except Exception as e:
        logger.warning("EDINET entity lookup failed for %s: %s", ticker, e)
        return None

    if entity is None:
        logger.warning("EDINET entity not found for ticker %s", ticker)
        return None

    try:
        docs = entity.documents(doc_type="120", days=max_age_days)
    except Exception as e:
        logger.warning("EDINET documents() failed for %s: %s", ticker, e)
        return None

    if not docs:
        logger.warning("No Securities Report found for %s in last %d days", ticker, max_age_days)
        return None

    # documents() typically returns newest-first
    try:
        report = docs[0].parse()
    except Exception as e:
        logger.warning("EDINET parse() failed for %s docID=%s: %s", ticker, getattr(docs[0], "doc_id", "?"), e)
        return None

    return report


def _find_raw(report, aliases: list[str]) -> float | None:
    """Search report.raw_fields for the first key matching any alias substring."""
    raw = getattr(report, "raw_fields", None) or {}
    lc_keys = {k.lower(): k for k in raw.keys()}
    for alias in aliases:
        # Direct match
        if alias in raw:
            return _as_float(raw[alias])
        # Substring match (case-insensitive)
        alias_lc = alias.lower()
        for kl, k_orig in lc_keys.items():
            if alias_lc in kl:
                return _as_float(raw[k_orig])
    return None


def _as_float(v) -> float | None:
    """Best-effort numeric coercion. Returns None if not coerceable."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        # XBRL values may be wrapped in dict/list — try to dig
        if isinstance(v, dict) and "value" in v:
            try:
                return float(v["value"])
            except (TypeError, ValueError):
                return None
        return None


def get_line_items_for_ticker(ticker: str, requested: list[str]) -> dict | None:
    """Return {line_item_name: float|None, "report_period": YYYY-MM-DD, ...} or None.

    Returns None if EDINET fetch failed entirely (no key, no docs, parse error).
    Returns dict with all requested items (None for items we couldn't resolve)
    when at least one item was extracted.
    """
    report = _fetch_latest_securities_report(ticker)
    if report is None:
        return None

    result: dict = {}

    # Period metadata
    fy_end = getattr(report, "fiscal_year_end", None)
    result["report_period"] = fy_end.isoformat() if fy_end else None
    result["currency"] = "JPY"
    result["accounting_standard"] = getattr(report, "accounting_standard", None)

    # Direct field map
    direct_map = {
        "depreciation_and_amortization": "depreciation_amortization",
        "net_income": "net_income",
        "total_assets": "total_assets",
        "total_liabilities": "total_liabilities",
        "shareholders_equity": "net_assets",  # JP "純資産" ≈ shareholders' equity
        "revenue": "net_sales",
    }

    for buffett_name in requested:
        if buffett_name in direct_map:
            v = getattr(report, direct_map[buffett_name], None)
            result[buffett_name] = _as_float(v)
        elif buffett_name == "free_cash_flow":
            ocf = _as_float(getattr(report, "operating_cash_flow", None))
            icf = _as_float(getattr(report, "investing_cash_flow", None))
            result[buffett_name] = (ocf + icf) if (ocf is not None and icf is not None) else None
            # Note: JP convention has investing CF typically negative for capex,
            # so OCF + ICF gives FCF directly (no subtraction inversion needed).
        elif buffett_name in _XBRL_ALIASES:
            result[buffett_name] = _find_raw(report, _XBRL_ALIASES[buffett_name])
        else:
            # Unknown name — best-effort raw_fields lookup with the name itself
            # converted to PascalCase-ish guesses
            guesses = [buffett_name, _snake_to_pascal(buffett_name)]
            result[buffett_name] = _find_raw(report, guesses)

    return result


_SNAKE_RE = re.compile(r"_([a-z])")


def _snake_to_pascal(snake: str) -> str:
    """net_income -> NetIncome (rough heuristic for raw_fields fallback)."""
    head = snake[:1].upper()
    tail = _SNAKE_RE.sub(lambda m: m.group(1).upper(), snake[1:])
    return head + tail
