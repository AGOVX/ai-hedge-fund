"""Watch List automated check — review deadlines, technicals, new disclosures.

Reads E:\\Company\\data\\watchlist.yaml and produces a markdown checkup report:

  1. Review deadlines  — next_review_due overdue / due within 7 days
  2. Technical signals — 200DMA position, 52w breakout/breakdown vs the
                         reentry/invalidation conditions recorded per PM
  3. New disclosures   — TDnet filings in the last N days (default 14)

The report is written to data/reports/watchlist-check-YYYYMMDD.md and printed.
This tool only OBSERVES and reports — re-analysis is still requested by the
shareholder and run by the CIO round-table (推奨止まりの原則).

Usage:
    python -m src.tools.watchlist_check            # live run
    python -m src.tools.watchlist_check --no-net   # deadlines only (no yfinance/TDnet)
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from src.tools.common import repo_root, write_report

logger = logging.getLogger(__name__)

_DUE_SOON_DAYS = 7
_DISCLOSURE_LOOKBACK_DAYS = 14


def watchlist_path() -> Path:
    env = os.environ.get("WATCHLIST_PATH", "")
    return Path(env) if env else repo_root() / "data" / "watchlist.yaml"


def load_watchlist(path: Path | None = None) -> list[dict]:
    p = path or watchlist_path()
    if not p.exists():
        logger.warning("Watchlist not found: %s", p)
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return data.get("watchlist", [])


# ---------------------------------------------------------------------------
# Checks (pure functions — testable without network)
# ---------------------------------------------------------------------------

def check_review_due(entry: dict, today: date) -> dict:
    """Classify the entry's next_review_due: overdue / due_soon / ok / unset."""
    raw = entry.get("next_review_due")
    if raw is None:
        return {"status": "unset", "due": None, "days_left": None}
    # datetime は date のサブクラスなので先に判定する (datetime - date は TypeError)
    if isinstance(raw, datetime):
        due = raw.date()
    elif isinstance(raw, date):
        due = raw
    else:
        due = date.fromisoformat(str(raw)[:10])
    days_left = (due - today).days
    if days_left < 0:
        status = "overdue"
    elif days_left <= _DUE_SOON_DAYS:
        status = "due_soon"
    else:
        status = "ok"
    return {"status": status, "due": due.isoformat(), "days_left": days_left}


def check_technical_signals(entry: dict, tech) -> list[str]:
    """Map computed Technicals onto the watchlist's recorded conditions.

    Returns human-readable alert strings (empty list = nothing notable).
    tech is a src.tools.technicals.Technicals or None.
    """
    alerts: list[str] = []
    if tech is None:
        return ["テクニカルデータ取得不可 (価格データなし)"]

    if tech.breakout_52w_high:
        vol = "出来高確認あり" if tech.breakout_volume_confirmed else "出来高確認なし"
        alerts.append(
            f"📈 52週高値ブレイクアウト: 終値 {tech.close:,.0f} > 前日まで高値 {tech.high_52w_prior:,.0f} ({vol})"
            " — Druckenmiller 再エントリー technical 条件に関連"
        )
    if tech.breakdown_52w_low:
        alerts.append(
            f"📉 52週安値割れ: 終値 {tech.close:,.0f} < 前日まで安値 {tech.low_52w_prior:,.0f}"
            " — Druckenmiller Invalidation (トレンド崩壊) に関連"
        )
    if tech.above_dma200 is False and tech.dma200_distance_pct is not None and tech.dma200_distance_pct < -10:
        alerts.append(f"⚠️ 200DMA を {abs(tech.dma200_distance_pct):.1f}% 下回る (長期トレンド弱含み)")

    # Price-trigger conditions: Buffett entries often record explicit price lines
    # like "株価 1,540 円以下". We scan required_price strings for thresholds.
    reentry = entry.get("reentry_conditions") or {}
    buffett = reentry.get("buffett") or {}
    for cond in buffett.get("required_price", []) or []:
        threshold = _extract_price_threshold(str(cond))
        if threshold and tech.close <= threshold:
            alerts.append(f"💰 Buffett 価格条件に到達: 終値 {tech.close:,.0f} ≤ {threshold:,.0f} 「{cond}」")
    return alerts


def _extract_price_threshold(cond: str) -> float | None:
    """'株価 1,540 円以下 (...)' -> 1540.0; None if no '円以下' pattern."""
    import re

    m = re.search(r"([\d,]+)\s*円以下", cond)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def filter_recent_disclosures(disclosures: list[dict], today: date, lookback_days: int = _DISCLOSURE_LOOKBACK_DAYS) -> list[dict]:
    """Keep disclosures whose pubdate falls within the lookback window."""
    cutoff = today - timedelta(days=lookback_days)
    out = []
    for d in disclosures:
        pub = str(d.get("pubdate", ""))[:10]
        try:
            if date.fromisoformat(pub) >= cutoff:
                out.append(d)
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(
    entries: list[dict],
    today: date,
    technicals_by_ticker: dict,
    disclosures_by_ticker: dict[str, list[dict]],
) -> str:
    lines = [
        f"# Watch List 自動点検レポート — {today.isoformat()}",
        "",
        "生成: `src/tools/watchlist_check.py` (観測のみ。再分析の発議は株主→CIO)",
        "",
    ]

    urgent: list[str] = []

    for e in entries:
        ticker = str(e.get("ticker", "?"))
        name = e.get("name", "")
        lines.append(f"## {ticker} {name} — status: {e.get('status', '?')}")
        lines.append("")

        # 1. review due
        due = check_review_due(e, today)
        if due["status"] == "overdue":
            msg = f"🔴 レビュー期日超過: {due['due']} ({-due['days_left']}日経過)"
            lines.append(f"- {msg}")
            urgent.append(f"{ticker} {name}: {msg}")
        elif due["status"] == "due_soon":
            msg = f"🟡 レビュー期日接近: {due['due']} (残り{due['days_left']}日)"
            lines.append(f"- {msg}")
            urgent.append(f"{ticker} {name}: {msg}")
        elif due["status"] == "ok":
            lines.append(f"- レビュー期日: {due['due']} (残り{due['days_left']}日)")
        else:
            lines.append("- レビュー期日: 未設定")

        # 2. technicals
        tech = technicals_by_ticker.get(ticker)
        alerts = check_technical_signals(e, tech)
        if tech is not None:
            if tech.above_dma200 is None:
                dma_part = "200DMA n/a (履歴200日未満)"
            else:
                dma_part = f"200DMA {tech.dma200:,.0f} の{'上' if tech.above_dma200 else '下'}"
            lines.append(
                f"- 終値 {tech.close:,.0f} ({tech.as_of}) / {dma_part} / "
                f"52週高値 {tech.high_52w:,.0f} 安値 {tech.low_52w:,.0f} / 高値乖離 {tech.pct_from_52w_high:+.1f}%"
            )
        for a in alerts:
            lines.append(f"- {a}")
            if a.startswith(("📈", "📉", "💰")):
                urgent.append(f"{ticker} {name}: {a}")

        # 3. recent disclosures
        recent = filter_recent_disclosures(disclosures_by_ticker.get(ticker, []), today)
        if recent:
            lines.append(f"- 直近{_DISCLOSURE_LOOKBACK_DAYS}日の適時開示 {len(recent)}件:")
            for d in recent[:8]:
                lines.append(f"  - {str(d.get('pubdate',''))[:10]} {d.get('title','')}")
            kessan = [d for d in recent if "決算" in d.get("title", "")]
            if kessan:
                urgent.append(f"{ticker} {name}: 🗞️ 決算関連開示あり「{kessan[0].get('title','')}」")
        else:
            lines.append(f"- 直近{_DISCLOSURE_LOOKBACK_DAYS}日の適時開示: なし (または未取得)")

        lines.append("")

    # Summary at top
    summary = ["## ⚡ 要対応サマリー", ""]
    if urgent:
        summary += [f"- {u}" for u in urgent]
    else:
        summary.append("- 特になし (全銘柄、条件未達・期日未到来)")
    summary.append("")

    return "\n".join(lines[:4] + summary + lines[4:]) + "\n"


def run(no_net: bool = False, today: date | None = None) -> Path:
    """Execute the full check and write the report. Returns the report path."""
    today = today or date.today()
    entries = load_watchlist()

    technicals_by_ticker: dict = {}
    disclosures_by_ticker: dict[str, list[dict]] = {}

    if not no_net:
        from src.tools.technicals import get_technicals
        from src.tools.tdnet import list_disclosures

        for e in entries:
            ticker = str(e.get("ticker", ""))
            if not ticker:
                continue
            technicals_by_ticker[ticker] = get_technicals(ticker)
            disclosures_by_ticker[ticker] = list_disclosures(ticker, limit=30)

    report = build_report(entries, today, technicals_by_ticker, disclosures_by_ticker)
    return write_report(f"watchlist-check-{today.strftime('%Y%m%d')}.md", report)


if __name__ == "__main__":
    import argparse

    from src.tools.common import utf8_stdout

    utf8_stdout()
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser(description="Watch List 自動点検")
    ap.add_argument("--no-net", action="store_true", help="ネットワークを使わず期日チェックのみ")
    args = ap.parse_args()
    run(no_net=args.no_net)
