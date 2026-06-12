"""決算発表日カレンダー — ハイブリッド方式 (手動記入 > yfinance 推定).

日本株の決算発表予定日は完全な無料 API が存在しないため:

  1. watchlist.yaml のエントリに `earnings_date: 2026-08-05` があればそれを採用 (手動)
  2. なければ yfinance の calendar から推定 (精度はまちまち — 過去日や欠損あり)

watchlist_check のレポートに「次回決算発表: 日付 (手動/推定)」として統合され、
7日以内なら要対応サマリーに上がる。発表予定が取れた銘柄はレビュー期日を
決算に合わせる判断材料になる (期日の変更自体は株主→CIO の判断)。
"""

from __future__ import annotations

import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

_EARNINGS_SOON_DAYS = 7


def _coerce_date(raw) -> date | None:
    """yaml/yfinance から来る datetime / date / 文字列を date に揃える。"""
    if raw is None:
        return None
    if isinstance(raw, datetime):  # datetime は date のサブクラス — 先に判定
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def next_earnings_from_yf(ticker: str) -> date | None:
    """yfinance calendar から将来の決算発表日を推定。失敗・過去日のみは None。"""
    import yfinance as yf

    try:
        cal = yf.Ticker(f"{ticker}.T").calendar
    except Exception as e:
        logger.warning("yfinance calendar 取得失敗 %s: %s", ticker, e)
        return None
    raw_dates = (cal or {}).get("Earnings Date") or []
    today = date.today()
    future = [d for d in (_coerce_date(r) for r in raw_dates) if d and d >= today]
    return min(future) if future else None


def resolve_earnings_date(entry: dict, today: date, yf_fetch=None) -> dict:
    """エントリの次回決算発表日を解決する。

    戻り値: {"date": "YYYY-MM-DD"|None, "source": "手動"|"推定"|None, "days_to": int|None}
    手動記入 (earnings_date) が常に優先。過去日の手動記入はそのまま出す
    (株主に更新を促すため) が、yfinance の過去日は捨てる。
    """
    manual = _coerce_date(entry.get("earnings_date"))
    if manual:
        return {"date": manual.isoformat(), "source": "手動", "days_to": (manual - today).days}

    fetch = yf_fetch or next_earnings_from_yf
    est = fetch(str(entry.get("ticker", "")))
    if est:
        return {"date": est.isoformat(), "source": "推定", "days_to": (est - today).days}
    return {"date": None, "source": None, "days_to": None}


def earnings_alert(info: dict) -> str | None:
    """7日以内 (当日含む) なら要対応アラート文字列、それ以外 None。"""
    days = info.get("days_to")
    if days is None or not info.get("date"):
        return None
    if days < 0:
        return f"🗓️ 決算発表日が過去 ({info['date']}, {info['source']}) — watchlist の earnings_date 更新を推奨"
    if days <= _EARNINGS_SOON_DAYS:
        return f"🗓️ 決算発表接近: {info['date']} (残り{days}日, {info['source']})"
    return None
