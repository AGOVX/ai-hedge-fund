"""Technical indicators for Watch List re-entry condition checks.

Computes the exact signals the watchlist reentry_conditions reference:
  - 200-day moving average (200DMA) and price position vs it
  - 52-week high / low and distance from current price
  - Breakout detection: close above prior 52w high with volume confirmation
  - Breakdown detection: close below prior 52w low

Price source: src.tools.jp_data.get_prices_jp (yfinance). All computation is
pure-python over the returned Price list so it is fully unit-testable with
synthetic data (compute_technicals takes the price list directly).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# Volume confirmation: breakout day volume >= this multiple of 20-day average
_VOLUME_CONFIRM_RATIO = 1.5


@dataclass
class Technicals:
    ticker: str
    as_of: str                      # date of the latest bar (YYYY-MM-DD)
    close: float
    dma200: float | None            # None when < 200 bars of history
    above_dma200: bool | None
    dma200_distance_pct: float | None   # (close - dma200) / dma200 * 100
    high_52w: float                 # max close of trailing 252 bars (incl. today);
                                    # 252 バー未満の銘柄は手持ち全期間 (bars 参照)
    low_52w: float
    high_52w_prior: float           # max close EXCLUDING the latest bar (breakout ref)
    low_52w_prior: float
    pct_from_52w_high: float        # (close - high_52w_prior) / high_52w_prior * 100
    volume_ratio_20d: float | None  # latest volume / 20-day avg volume
    breakout_52w_high: bool         # close > prior 52w high (252バー揃うまで常に False)
    breakout_volume_confirmed: bool # breakout AND volume >= 1.5x 20d avg
    breakdown_52w_low: bool         # close < prior 52w low (252バー揃うまで常に False)
    bars: int = 0                   # 計算に使ったバー数 (上場1年未満の検知用)

    def to_dict(self) -> dict:
        return asdict(self)


def compute_technicals(ticker: str, prices: list) -> Technicals | None:
    """Compute indicators from a chronological list of Price objects.

    Accepts any objects with .close, .volume, .time attributes (src.data.models.Price).
    Returns None when fewer than 20 bars are available.
    """
    if len(prices) < 20:
        logger.warning("Not enough price history for %s (%d bars)", ticker, len(prices))
        return None

    closes = [p.close for p in prices]
    volumes = [p.volume for p in prices]

    latest_close = closes[-1]
    as_of = str(prices[-1].time)[:10]

    # 200DMA
    dma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
    above = (latest_close > dma200) if dma200 is not None else None
    dist = ((latest_close - dma200) / dma200 * 100) if dma200 else None

    # 52-week window ~ 252 trading days
    window = closes[-252:]
    high_52w = max(window)
    low_52w = min(window)
    prior_window = closes[-253:-1]  # up to 252 bars excluding the latest (>=19 bars here)
    high_prior = max(prior_window)
    low_prior = min(prior_window)

    # Volume confirmation
    vol20 = [v for v in volumes[-21:-1] if v]  # previous 20 bars, skip zero/None
    vol_ratio = (volumes[-1] / (sum(vol20) / len(vol20))) if vol20 and volumes[-1] else None

    # 上場1年未満 (252バー未満) は「52週」窓が不完全 — 新値のたびに偽ブレイクアウトに
    # なるため、フル窓が揃うまでブレイク判定を抑止する
    full_window = len(closes) >= 252
    breakout = full_window and latest_close > high_prior
    breakdown = full_window and latest_close < low_prior

    return Technicals(
        ticker=ticker,
        as_of=as_of,
        close=round(latest_close, 2),
        dma200=round(dma200, 2) if dma200 is not None else None,
        above_dma200=above,
        dma200_distance_pct=round(dist, 2) if dist is not None else None,
        high_52w=round(high_52w, 2),
        low_52w=round(low_52w, 2),
        high_52w_prior=round(high_prior, 2),
        low_52w_prior=round(low_prior, 2),
        pct_from_52w_high=round((latest_close - high_prior) / high_prior * 100, 2),
        volume_ratio_20d=round(vol_ratio, 2) if vol_ratio is not None else None,
        breakout_52w_high=breakout,
        breakout_volume_confirmed=breakout and vol_ratio is not None and vol_ratio >= _VOLUME_CONFIRM_RATIO,
        breakdown_52w_low=breakdown,
        bars=len(closes),
    )


def get_technicals(ticker: str, lookback_days: int = 420) -> Technicals | None:
    """Fetch prices via jp_data (yfinance) and compute indicators.

    lookback_days=420 calendar days ≈ 280+ trading days, enough for 200DMA + 52w window.
    """
    from src.tools.jp_data import get_prices_jp

    end = date.today() + timedelta(days=1)
    start = end - timedelta(days=lookback_days)
    prices = get_prices_jp(ticker, start.isoformat(), end.isoformat())
    if not prices:
        logger.warning("No price data for %s — technicals unavailable", ticker)
        return None
    return compute_technicals(ticker, prices)
