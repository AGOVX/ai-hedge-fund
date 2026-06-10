"""EDINET XBRL adapter for Japanese line-item extraction.

Wraps `edinet-tools` (matthelmer) to provide Buffett's 12 line items from the
latest Securities Report (有価証券報告書, doc_type='120') of a given JP ticker.

Reads EDINET_API_KEY via os.environ.get() (never hardcoded), loading .env at
import so the CLI tools work without main.py. When the key is absent the
module degrades to a warning + empty result so the rest of the pipeline
keeps running.

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
from datetime import date, timedelta
from pathlib import Path

from src.tools import filings_store

try:  # CLI 実行でも .env の EDINET_API_KEY を拾う (既存の環境変数は上書きしない)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# doc_id of the latest fetched Securities Report per ticker, populated by
# _fetch_latest_securities_report (stays empty when that function is mocked).
_DOC_IDS: dict[str, str] = {}

# Latest 有報の提出日と EDINET コード (過去期の周年窓プローブ用)
_SUBMIT_DATES: dict[str, "date"] = {}
_EDINET_CODES: dict[str, str] = {}

# In-process cache of parsed SecuritiesReports. Successes ONLY — failures
# (missing key, network error) are NOT cached so a transient error doesn't
# poison the rest of the process. (lru_cache would cache the None too.)
_REPORT_CACHE: dict[str, object] = {}

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


def _fetch_latest_securities_report(ticker: str, max_age_days: int = 540):
    """Fetch the latest Securities Report (有報, doc_type=120) for a ticker.

    Returns a parsed SecuritiesReport or None on any failure (missing key,
    no filings, parse error). Successful results are cached in-process per
    ticker; failures are retried on the next call.
    """
    if ticker in _REPORT_CACHE:
        return _REPORT_CACHE[ticker]

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

    doc_id = getattr(docs[0], "doc_id", None)
    if doc_id:
        _DOC_IDS[ticker] = str(doc_id)
    submit_dt = getattr(docs[0], "filing_datetime", None)
    if submit_dt:
        _SUBMIT_DATES[ticker] = submit_dt.date()
    edinet_code = getattr(entity, "edinet_code", None)
    if edinet_code:
        _EDINET_CODES[ticker] = str(edinet_code)

    # documents() typically returns newest-first
    try:
        report = docs[0].parse()
    except Exception as e:
        logger.warning("EDINET parse() failed for %s docID=%s: %s", ticker, getattr(docs[0], "doc_id", "?"), e)
        return None

    _REPORT_CACHE[ticker] = report
    return report


def _report_cache_clear() -> None:
    _REPORT_CACHE.clear()
    _DOC_IDS.clear()
    _SUBMIT_DATES.clear()
    _EDINET_CODES.clear()


# lru_cache 互換 API (テスト等が cache_clear() を呼ぶ)
_fetch_latest_securities_report.cache_clear = _report_cache_clear


def _ratio_like(key: str) -> bool:
    """比率/マージン系の派生要素か (金額を探しているときに誤って拾わないため)."""
    kl = key.lower()
    return any(w in kl for w in ("margin", "ratio", "percentage", "rate"))


def _find_raw(report, aliases: list[str]) -> float | None:
    """Search report.raw_fields for the first key matching any alias.

    Match order per alias: exact key → case-insensitive substring。substring の
    候補が複数あるときは (比率系でない, キー長が短い, 辞書順) で決定論的に選ぶ —
    'GrossProfit' を探して 'GrossProfitMarginIA' (比率) を拾う事故を防ぐ。
    """
    raw = getattr(report, "raw_fields", None) or {}
    for alias in aliases:
        # Direct match
        if alias in raw:
            return _as_float(raw[alias])
        # Substring match (case-insensitive, deterministic preference)
        alias_lc = alias.lower()
        candidates = sorted(
            (k for k in raw if alias_lc in k.lower()),
            key=lambda k: (_ratio_like(k), len(k), k),
        )
        if candidates:
            return _as_float(raw[candidates[0]])
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


def _extract_items(report, requested: list[str]) -> dict:
    """SecuritiesReport から requested の項目を抽出 (期メタデータ付き)。"""
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


def get_line_items_for_ticker(ticker: str, requested: list[str]) -> dict | None:
    """Return {line_item_name: float|None, "report_period": YYYY-MM-DD, ...} or None.

    Returns None if EDINET fetch failed entirely (no key, no docs, parse error).
    Returns dict with all requested items (None for items we couldn't resolve)
    when at least one item was extracted.

    Persistent cache: extracted payloads are stored via filings_store, so a
    second call (even in a new process) is served from SQLite without hitting
    EDINET. Cache TTL is EDINET_CACHE_DAYS (default 30).
    """
    try:
        cache_days = int(os.environ.get("EDINET_CACHE_DAYS", "30"))
    except ValueError:
        logger.warning("EDINET_CACHE_DAYS が数値でない — 既定の 30 日を使用")
        cache_days = 30
    cached = filings_store.load_line_items(ticker, max_age_days=cache_days)
    if cached is not None and all(k in cached for k in requested):
        logger.info("EDINET line-items cache hit for %s (period=%s)", ticker, cached.get("report_period"))
        return {k: cached[k] for k in requested} | {
            k: cached.get(k) for k in ("report_period", "currency", "accounting_standard")
        }

    report = _fetch_latest_securities_report(ticker)
    if report is None:
        return None

    result = _extract_items(report, requested)

    # Persist so future runs (any process) skip the EDINET round-trip
    doc_id = _DOC_IDS.get(ticker) or f"period-{result.get('report_period') or 'unknown'}"
    try:
        filings_store.save_line_items(ticker, doc_id, result)
    except Exception as e:
        logger.warning("Failed to persist line items for %s: %s", ticker, e)

    return result


# ---------------------------------------------------------------------------
# 過去期の有報時系列 (Buffett 流の ROE 10年安定性評価用)
# ---------------------------------------------------------------------------

_HISTORY_MARKER = "__history_scan__"
_PROBE_WINDOW_DAYS = 12  # 周年日 ± この日数を外側に向かって探索


def _probe_yuho_doc(edinet_code: str, around: date, window: int = _PROBE_WINDOW_DAYS):
    """around の前後 window 日を近い順に走査し、該当法人の有報 (120) を探す。

    有報の提出日は毎年ほぼ同時期 (3月期 → 6月下旬) なので、全日付走査
    (10年 = 3,650 リクエスト) ではなく周年窓だけを叩く (典型 数リクエスト/期)。
    戻り値は (doc, 提出日) — 提出日は呼び出し側が次の期のチェーン基準に使う。
    """
    import edinet_tools

    today = date.today()
    offsets = [0]
    for o in range(1, window + 1):
        offsets += [o, -o]
    for off in offsets:
        d = around + timedelta(days=off)
        if d > today:
            continue
        try:
            docs = edinet_tools.documents(date=d.isoformat(), doc_type="120")
        except Exception as e:
            logger.warning("EDINET documents(%s) failed: %s", d, e)
            continue
        for doc in docs:
            if getattr(doc, "filer_edinet_code", None) == edinet_code:
                return doc, d
    return None


def get_line_items_history(
    ticker: str, requested: list[str], periods: int = 10
) -> list[dict]:
    """過去 periods 期分の有報 line-items を期末降順で返す (取れた分だけ)。

    - 過去の有報は不変なので SQLite キャッシュに TTL なしで永続化
    - 走査済みマーカーを残し、上場年数 < periods の銘柄で毎回プローブし直さない
    - EDINET キーが無い場合などはキャッシュ分のみ返す (空 list あり得る)
    """
    cached = filings_store.load_line_items_history(ticker)
    usable = [p for p in cached if p.get("report_period") and all(k in p for k in requested)]
    marker = filings_store.load_line_items_by_doc(ticker, _HISTORY_MARKER) or {}
    if len(usable) >= periods or marker.get("scanned_periods", 0) >= periods:
        return usable[:periods]

    # --- 最新期 (既存経路で取得・保存) ---
    latest = get_line_items_for_ticker(ticker, requested)
    if latest is None:
        logger.warning("%s: 最新有報が取得できず — キャッシュ %d 期分のみ返す", ticker, len(usable))
        return usable[:periods]

    edinet_code = _EDINET_CODES.get(ticker)
    anchor = _SUBMIT_DATES.get(ticker)
    if not edinet_code or not anchor:
        # キャッシュヒットで EDINET に行かなかった場合は実フェッチでメタを取る
        _report_cache_clear()
        if _fetch_latest_securities_report(ticker) is None:
            return usable[:periods]
        edinet_code = _EDINET_CODES.get(ticker)
        anchor = _SUBMIT_DATES.get(ticker)
        if not edinet_code or not anchor:
            return usable[:periods]

    # 取得済み年度の判定は「期末の年」で行う (12月期決算は提出年 ≠ 期末年)
    have_years = {
        str(p["report_period"])[:4]
        for p in usable + [latest]
        if p.get("report_period")
    }
    latest_fy_year = (
        int(str(latest["report_period"])[:4]) if latest.get("report_period") else anchor.year
    )

    # --- 過去期を周年窓でプローブ ---
    # チェーン式: 探索基準を「直前の期で実際に見つかった提出日」に更新していく。
    # 提出日は年により 2 週間程度ドリフトする (4751 実測: 12/8〜12/22) ため、
    # 最新提出日からの固定周年 (anchor - 365.25k) では窓 ±12 日を外す年が出る。
    found = 0
    target = anchor
    for k in range(1, periods):
        target -= timedelta(days=365)
        if str(latest_fy_year - k) in have_years:
            continue  # この年度は取得済み (提出日不明のままチェーンを進める)
        hit = _probe_yuho_doc(edinet_code, target)
        if hit is None:
            logger.info("%s: %d 期前 (%s 近辺) の有報が見つからない", ticker, k, target)
            continue
        doc, submitted = hit
        target = submitted  # 次の期はこの提出日の周年から探す
        try:
            report = doc.parse()
        except Exception as e:
            logger.warning("%s: docID=%s parse 失敗: %s", ticker, getattr(doc, "doc_id", "?"), e)
            continue
        payload = _extract_items(report, requested)
        if not payload.get("report_period"):
            payload["report_period"] = getattr(doc, "period_end", None)
        try:
            filings_store.save_line_items(ticker, str(doc.doc_id), payload)
        except Exception as e:
            logger.warning("%s: 過去期 line-items の保存失敗: %s", ticker, e)
        if payload.get("report_period"):
            have_years.add(str(payload["report_period"])[:4])
        found += 1

    # 走査完了マーカー (上場が浅く periods 期分無い銘柄の再走査防止)
    try:
        filings_store.save_line_items(
            ticker, _HISTORY_MARKER,
            {"scanned_periods": periods, "found": found, "report_period": None},
        )
    except Exception as e:
        logger.warning("%s: 履歴マーカーの保存失敗: %s", ticker, e)

    refreshed = filings_store.load_line_items_history(ticker)
    usable = [p for p in refreshed if p.get("report_period") and all(k in p for k in requested)]
    return usable[:periods]


def build_history_report(ticker: str, payloads: list[dict]) -> str:
    """期末降順の payloads から ROE 推移等の Markdown テーブルを作る (pure)。"""
    lines = [
        f"# 財務時系列 — {ticker} ({len(payloads)} 期分)",
        "",
        "出所: EDINET 有価証券報告書 XBRL。単位: 億円 (1e8 JPY)。",
        "",
        "| 期末 | 売上高 | 純利益 | 純利益率 | ROE | 自己資本比率 | FCF |",
        "|---|---|---|---|---|---|---|",
    ]

    def _oku(v) -> str:
        return f"{v / 1e8:,.0f}" if v is not None else "—"

    def _pct(num, den) -> str:
        return f"{num / den * 100:.1f}%" if num is not None and den else "—"

    for p in payloads:
        rev = p.get("revenue")
        ni = p.get("net_income")
        eq = p.get("shareholders_equity")
        ta = p.get("total_assets")
        fcf = p.get("free_cash_flow")
        lines.append(
            f"| {p.get('report_period', '?')} | {_oku(rev)} | {_oku(ni)} "
            f"| {_pct(ni, rev)} | {_pct(ni, eq)} | {_pct(eq, ta)} | {_oku(fcf)} |"
        )

    roes = [
        p["net_income"] / p["shareholders_equity"] * 100
        for p in payloads
        if p.get("net_income") is not None and p.get("shareholders_equity")
    ]
    if roes:
        lines += [
            "",
            f"ROE: 平均 {sum(roes) / len(roes):.1f}% / 最低 {min(roes):.1f}% / 最高 {max(roes):.1f}% "
            f"({len(roes)} 期)",
        ]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 有報 PDF download (qualitative sections: 経営方針 / リスク / MD&A)
# ---------------------------------------------------------------------------

_EDINET_DOC_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"


def fetch_yuho_pdf(ticker: str) -> Path | None:
    """Download the latest Securities Report PDF for a ticker; cached on disk.

    Returns the local PDF path or None (no key / no filing / download failure).
    A second call for the same doc_id is served from filings_store without
    network access.
    """
    cached = filings_store.find_filing(ticker, doc_type="yuho_pdf", source="edinet")

    if not _has_api_key():
        logger.warning("EDINET_API_KEY not set — fetch_yuho_pdf skipped.")
        return Path(cached["file_path"]) if cached else None

    # Resolve the latest doc_id (also warms the line-items fetch path)
    report = _fetch_latest_securities_report(ticker)
    doc_id = _DOC_IDS.get(ticker)
    if doc_id is None:
        logger.warning("No EDINET doc_id resolved for %s — cannot fetch PDF", ticker)
        return Path(cached["file_path"]) if cached else None

    if cached and cached["doc_id"] == doc_id:
        logger.info("有報 PDF cache hit for %s: %s", ticker, cached["file_path"])
        return Path(cached["file_path"])

    import httpx

    try:
        resp = httpx.get(
            _EDINET_DOC_URL.format(doc_id=doc_id),
            params={"type": "2", "Subscription-Key": os.environ["EDINET_API_KEY"]},
            timeout=60.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("EDINET PDF download failed for %s docID=%s: %s", ticker, doc_id, e)
        return Path(cached["file_path"]) if cached else None

    if not resp.content.startswith(b"%PDF"):
        logger.warning("EDINET returned non-PDF content for %s docID=%s — discarding", ticker, doc_id)
        return Path(cached["file_path"]) if cached else None

    dest = filings_store.ticker_dir(ticker) / f"{doc_id}_yuho.pdf"
    dest.write_bytes(resp.content)

    fy_end = getattr(report, "fiscal_year_end", None) if report is not None else None
    filings_store.record_filing(
        ticker=ticker,
        source="edinet",
        doc_type="yuho_pdf",
        doc_id=doc_id,
        file_path=dest,
        period=fy_end.isoformat() if fy_end else None,
        title="有価証券報告書",
    )
    logger.info("Downloaded 有報 PDF for %s: %s", ticker, dest)
    return dest


_SNAKE_RE = re.compile(r"_([a-z])")


def _snake_to_pascal(snake: str) -> str:
    """net_income -> NetIncome (rough heuristic for raw_fields fallback)."""
    head = snake[:1].upper()
    tail = _SNAKE_RE.sub(lambda m: m.group(1).upper(), snake[1:])
    return head + tail
