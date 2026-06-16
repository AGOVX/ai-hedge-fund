"""TDnet 決算短信サマリー iXBRL から四半期決算の数値を抽出する.

EDINET は四半期報告書を 2024年4月に廃止し、Q1/Q3 の正式な構造化開示が
無くなった (半期報告書=H1 と 有報=通期 のみ XBRL 化される)。一方 TDnet の
決算短信には Q1〜Q4 すべてにサマリー iXBRL が付随し、売上・営業利益・
経常利益・親会社株主帰属利益・総資産・純資産・通期会社予想 (+前年同期) を
機械可読で持つ。本モジュールはこれを唯一の「全四半期を埋められる無料・
一次情報」経路として利用する。

制約:
  - TDnet の zip (release.tdnet.info) は開示から約31日で消える。よって
    「鮮度のあるうちに取得して SQLite に永続化」する運用が前提
    (daily_check / watchlist_check の定期ジョブが各四半期を取りこぼさない)。
  - キャッシュフローは四半期短信サマリーには通常含まれない (Q1/Q3 は CF 非開示)。
    FCF が要るH1は EDINET 半期報告書 (edinet.get_latest_interim_for_ticker) を使う。

XBRL コンテキスト命名 (tse-ed-t タクソノミ):
    CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember  当期累計(実績)
    PriorAccumulatedQ2Duration_ConsolidatedMember_ResultMember    前年同期(実績)
    CurrentYearDuration_ConsolidatedMember_ForecastMember         通期会社予想
    CurrentAccumulatedQ2Instant_ConsolidatedMember_ResultMember   当期末BS
"""

from __future__ import annotations

import io
import logging
import re
import zipfile

from src.tools import filings_store, tdnet

logger = logging.getLogger(__name__)

_TIMEOUT = 60.0
_UA = {"User-Agent": "Mozilla/5.0 (filings-fetcher; personal research; low-frequency)"}
_TANSHIN_RE = re.compile(r"決算短信")
_CORRECTION_RE = re.compile(r"訂正")
_QUARTER_STALE_DAYS = 95  # キャッシュの期末がこれより新しければ再取得しない (四半期≒91日)

_NONFRACTION_RE = re.compile(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.I | re.S)
_NONNUMERIC_RE = re.compile(r"<ix:nonNumeric\b([^>]*)>(.*?)</ix:nonNumeric>", re.I | re.S)
_ATTR_RE = re.compile(r'([\w:]+)\s*=\s*"([^"]*)"')
_TAG_RE = re.compile(r"<[^>]+>")
_MEMBERS = ("ConsolidatedMember", "NonConsolidatedMember")

# Buffett 項目 → tse-ed-t 要素ローカル名 (先頭が優先)
_REVENUE = ("NetSales", "OperatingRevenues", "GrossOperatingRevenues", "NetSalesOfCompletedConstructionContracts", "Revenue")
_NET_INCOME = ("ProfitAttributableToOwnersOfParent", "NetIncome")


# ---------------------------------------------------------------------------
# iXBRL parsing (pure — unit-testable)
# ---------------------------------------------------------------------------

def _ixbrl_facts(html: str) -> dict:
    """ix:nonFraction を {(要素ローカル名, contextRef): float} に索引化する。

    scale (10^n 倍) と sign='-' を適用。カンマ区切りを除去。値が数値でない
    fact は捨てる。
    """
    facts: dict = {}
    for m in _NONFRACTION_RE.finditer(html):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        name = attrs.get("name", "").split(":")[-1]
        ctx = attrs.get("contextRef", "")
        raw = _TAG_RE.sub("", m.group(2)).replace(",", "").strip()
        if not name or not ctx or not raw:
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        try:
            val *= 10 ** int(attrs.get("scale", "0") or 0)
        except ValueError:
            pass
        if attrs.get("sign") == "-":
            val = -val
        facts[(name, ctx)] = val
    return facts


def _ixbrl_nonnumeric(html: str) -> dict:
    """ix:nonNumeric を {要素ローカル名: テキスト} に (最初の出現を採用)。"""
    out: dict = {}
    for m in _NONNUMERIC_RE.finditer(html):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        name = attrs.get("name", "").split(":")[-1]
        txt = _TAG_RE.sub("", m.group(2)).strip()
        if name and txt:
            out.setdefault(name, txt)
    return out


def _pick(facts: dict, elements, period_token: str, time_token: str,
          result_token: str = "ResultMember"):
    """要素優先 × 連結優先で、指定コンテキスト群に一致する最初の値を返す。

    period_token: 'Current' | 'Prior' | 'CurrentYear' など (前方一致)
    time_token: 'Duration' (フロー) | 'Instant' (ストック)
    """
    for member in _MEMBERS:
        rx = re.compile(rf"^{period_token}\w*{time_token}_{member}_{result_token}$")
        for el in elements:
            for (name, ctx), val in facts.items():
                if name == el and rx.match(ctx):
                    return val
    return None


def _period_type(facts: dict) -> str | None:
    """当期のフロー・コンテキストから Q1/Q2/Q3/FY を判定。"""
    for (_name, ctx) in facts:
        m = re.match(r"^CurrentAccumulatedQ(\d)Duration_", ctx)
        if m:
            return f"Q{m.group(1)}"
        if re.match(r"^CurrentYearDuration_", ctx):
            return "FY"
    return None


def parse_summary_ixbrl(html: str, period_end: str | None = None) -> dict:
    """短信サマリー iXBRL から当期実績・前年同期・通期予想を抽出する (pure)。

    period_end は zip メンバー名から渡す (サマリーに明示の期末日要素が無いため)。
    """
    facts = _ixbrl_facts(html)
    meta = _ixbrl_nonnumeric(html)

    def cur_dur(elems):
        return _pick(facts, elems, "Current", "Duration")

    def cur_inst(elems):
        return _pick(facts, elems, "Current", "Instant")

    out: dict = {
        "source": "tdnet",
        "report_period": period_end,
        "period_type": _period_type(facts),
        "fiscal_year_end": meta.get("FiscalYearEnd"),
        "cumulative": True,
        "revenue": cur_dur(_REVENUE),
        "operating_income": cur_dur(("OperatingIncome",)),
        "ordinary_income": cur_dur(("OrdinaryIncome",)),
        "net_income": cur_dur(_NET_INCOME),
        "total_assets": cur_inst(("TotalAssets",)),
        "net_assets": cur_inst(("NetAssets",)),
        # CF は短信サマリーには通常含まれない (FCF が要るH1は EDINET 半期を使う)
        "operating_cf": cur_dur(("CashFlowsFromOperatingActivities",)),
        "investing_cf": cur_dur(("CashFlowsFromInvestingActivities",)),
    }
    ta, na = out["total_assets"], out["net_assets"]
    out["equity_ratio_pct"] = round(na / ta * 100, 1) if (na is not None and ta) else None
    ocf, icf = out["operating_cf"], out["investing_cf"]
    out["free_cash_flow"] = (ocf + icf) if (ocf is not None and icf is not None) else None

    out["prior"] = {
        "revenue": _pick(facts, _REVENUE, "Prior", "Duration"),
        "operating_income": _pick(facts, ("OperatingIncome",), "Prior", "Duration"),
        "ordinary_income": _pick(facts, ("OrdinaryIncome",), "Prior", "Duration"),
        "net_income": _pick(facts, _NET_INCOME, "Prior", "Duration"),
    }
    # 通期会社予想 (レンジ予想の場合は Lower を保守的に採用)
    def fc(elems):
        v = _pick(facts, elems, "CurrentYear", "Duration", result_token="ForecastMember")
        return v if v is not None else _pick(facts, elems, "CurrentYear", "Duration", result_token="LowerMember")

    out["forecast_fy"] = {
        "revenue": fc(_REVENUE),
        "operating_income": fc(("OperatingIncome",)),
        "ordinary_income": fc(("OrdinaryIncome",)),
        "net_income": fc(_NET_INCOME),
    }
    return out


# ---------------------------------------------------------------------------
# Fetch (network) + persist
# ---------------------------------------------------------------------------

def _period_end_from_names(names: list[str]) -> str | None:
    """zip メンバー名に現れる最初の YYYY-MM-DD (期末日) を返す。"""
    for n in names:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", n)
        if m:
            return m.group(1)
    return None


def _summary_html_from_zip(content: bytes) -> tuple[str, list[str]] | None:
    """短信 zip から Summary iXBRL htm を取り出す。"""
    try:
        z = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return None
    names = z.namelist()
    summ = [n for n in names if n.endswith("ixbrl.htm") and "Summary" in n]
    if not summ:
        return None
    return z.read(summ[0]).decode("utf-8", "replace"), names


def fetch_latest_quarter(ticker: str, refresh: bool = False) -> dict | None:
    """最新の決算短信サマリー XBRL を取得・解析して当期実績等を返す。

    結果は interim_items テーブルに永続化 (source='tdnet')。TDnet zip は
    約31日で消えるため、取得できた時に必ずキャッシュへ残す。期末が
    _QUARTER_STALE_DAYS より新しければ既存キャッシュを返す。
    """
    import httpx

    cached = filings_store.load_latest_interim(ticker, source="tdnet")
    if cached and not refresh:
        from datetime import date
        pe = cached.get("report_period")
        try:
            if pe and (date.today() - date.fromisoformat(pe)).days <= _QUARTER_STALE_DAYS:
                logger.info("quarter cache hit for %s (period=%s)", ticker, pe)
                return cached
        except ValueError:
            pass

    disclosures = tdnet.list_disclosures(ticker, limit=30)
    # 訂正でない決算短信を新しい順に。url_xbrl があるものだけ。
    cands = [d for d in disclosures
             if _TANSHIN_RE.search(d.get("title", ""))
             and not _CORRECTION_RE.search(d.get("title", ""))
             and d.get("url_xbrl")]
    if not cands:
        logger.warning("%s: url_xbrl 付き決算短信が見つからない", ticker)
        return cached

    latest = cands[0]
    try:
        resp = httpx.get(latest["url_xbrl"], timeout=_TIMEOUT, headers=_UA, follow_redirects=True)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("%s: 短信 XBRL zip の取得失敗 (%s) — 期限切れ(>31日)の可能性", ticker, e)
        return cached

    extracted = _summary_html_from_zip(resp.content)
    if extracted is None:
        logger.warning("%s: 短信 zip に Summary iXBRL が無い", ticker)
        return cached
    html, names = extracted

    payload = parse_summary_ixbrl(html, period_end=_period_end_from_names(names))
    if not payload.get("report_period"):
        logger.warning("%s: 短信 XBRL から期末日を特定できず", ticker)
        return cached
    try:
        filings_store.save_interim_items(ticker, str(latest["doc_id"]), payload)
    except Exception as e:
        logger.warning("%s: 四半期 payload 保存失敗: %s", ticker, e)
    return payload


def quarter_summary_line(payload: dict) -> str | None:
    """watchlist 等に1行で出す要約 (なければ None)。"""
    if not payload or not payload.get("report_period"):
        return None
    label = {"Q1": "Q1", "Q2": "Q2(中間)", "Q3": "Q3", "FY": "通期"}.get(payload.get("period_type") or "", "")
    prior = payload.get("prior") or {}
    fc = payload.get("forecast_fy") or {}

    def _yoy(cur, pri):
        if cur is None or pri in (None, 0):
            return None
        return (cur - pri) / abs(pri) * 100

    parts = [f"最新決算 {label} ({payload['report_period']})"]
    rev_yoy = _yoy(payload.get("revenue"), prior.get("revenue"))
    ni_yoy = _yoy(payload.get("net_income"), prior.get("net_income"))
    if rev_yoy is not None:
        parts.append(f"売上{rev_yoy:+.1f}%")
    if ni_yoy is not None:
        parts.append(f"純益{ni_yoy:+.1f}%")
    op, opf = payload.get("operating_income"), fc.get("operating_income")
    if op is not None and opf:
        parts.append(f"通期営業益進捗{op / opf * 100:.0f}%")
    return " / ".join(parts)


def build_quarter_report(ticker: str, payload: dict) -> str:
    """四半期決算 payload から当期 vs 前年同期 + 通期進捗の Markdown (pure)。"""
    ptype = payload.get("period_type") or "中間"
    label = {"Q1": "第1四半期", "Q2": "第2四半期(中間)", "Q3": "第3四半期", "FY": "通期"}.get(ptype, ptype)
    lines = [
        f"# 最新四半期決算 — {ticker} ({label})",
        "",
        f"期末: {payload.get('report_period', '?')} (通期末 {payload.get('fiscal_year_end', '?')})。"
        " 出所: TDnet 決算短信サマリー XBRL。単位: 億円、期初からの累計。",
        "",
        "| 項目 | 当期累計 | 前年同期 | YoY | 通期会社予想 | 進捗率 |",
        "|---|---|---|---|---|---|",
    ]
    prior = payload.get("prior") or {}
    fc = payload.get("forecast_fy") or {}

    def _oku(v):
        return f"{v / 1e8:,.0f}" if v is not None else "—"

    def _yoy(cur, pri):
        if cur is None or pri in (None, 0):
            return "—"
        return f"{(cur - pri) / abs(pri) * 100:+.1f}%"

    def _prog(cur, f):
        if cur is None or f in (None, 0):
            return "—"
        return f"{cur / f * 100:.0f}%"

    for jp, key in (("売上高", "revenue"), ("営業利益", "operating_income"),
                    ("経常利益", "ordinary_income"), ("純利益(親)", "net_income")):
        cur = payload.get(key)
        lines.append(
            f"| {jp} | {_oku(cur)} | {_oku(prior.get(key))} | {_yoy(cur, prior.get(key))} "
            f"| {_oku(fc.get(key))} | {_prog(cur, fc.get(key))} |"
        )
    er = payload.get("equity_ratio_pct")
    lines += [
        "",
        f"期末BS: 総資産 {_oku(payload.get('total_assets'))}億 / 純資産 {_oku(payload.get('net_assets'))}億 "
        f"/ 自己資本比率 {er if er is not None else '—'}%",
        "⚠ CFは四半期短信では非開示が多い。FCFが要るH1は `--interim` (EDINET半期) を併用。",
        "",
    ]
    return "\n".join(lines)
