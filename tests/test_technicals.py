"""Unit tests for src/tools/technicals.py — pure computation, no network."""
from dataclasses import dataclass

from src.tools.technicals import compute_technicals


@dataclass
class FakePrice:
    close: float
    volume: int
    time: str


def _series(closes, volumes=None, start="2025-01-01"):
    volumes = volumes or [100_000] * len(closes)
    return [FakePrice(close=c, volume=v, time=f"bar-{i:04d}") for i, (c, v) in enumerate(zip(closes, volumes))]


class TestBasics:
    def test_too_few_bars_returns_none(self):
        assert compute_technicals("4751", _series([100.0] * 19)) is None

    def test_no_dma200_under_200_bars(self):
        t = compute_technicals("4751", _series([100.0] * 100))
        assert t is not None
        assert t.dma200 is None
        assert t.above_dma200 is None

    def test_dma200_flat_series(self):
        t = compute_technicals("4751", _series([100.0] * 260))
        assert t.dma200 == 100.0
        assert t.above_dma200 is False  # equal is not above
        assert t.high_52w == 100.0
        assert t.low_52w == 100.0

    def test_above_dma200(self):
        closes = [100.0] * 259 + [120.0]
        t = compute_technicals("4751", _series(closes))
        assert t.above_dma200 is True
        assert t.dma200_distance_pct > 0


class TestBreakout:
    def test_breakout_with_volume(self):
        closes = [100.0] * 259 + [130.0]            # prior 52w high = 100
        volumes = [100_000] * 259 + [200_000]       # 2.0x the 20d avg
        t = compute_technicals("4751", _series(closes, volumes))
        assert t.breakout_52w_high is True
        assert t.breakout_volume_confirmed is True
        assert t.high_52w_prior == 100.0
        assert t.high_52w == 130.0                  # incl. today

    def test_breakout_without_volume(self):
        closes = [100.0] * 259 + [130.0]
        volumes = [100_000] * 259 + [110_000]       # only 1.1x
        t = compute_technicals("4751", _series(closes, volumes))
        assert t.breakout_52w_high is True
        assert t.breakout_volume_confirmed is False

    def test_no_breakout(self):
        closes = [100.0] * 200 + [150.0] + [120.0] * 59
        t = compute_technicals("4751", _series(closes))
        assert t.breakout_52w_high is False
        assert t.high_52w_prior == 150.0
        assert t.pct_from_52w_high == -20.0

    def test_breakdown(self):
        closes = [100.0] * 259 + [80.0]
        t = compute_technicals("4751", _series(closes))
        assert t.breakdown_52w_low is True
        assert t.low_52w_prior == 100.0

    def test_52w_window_excludes_old_bars(self):
        # A 300-bar-old spike must NOT count toward the 52w (252-bar) window
        closes = [500.0] + [100.0] * 299
        t = compute_technicals("4751", _series(closes))
        assert t.high_52w == 100.0


class TestVolumeRatio:
    def test_ratio_computed(self):
        closes = [100.0] * 30
        volumes = [100_000] * 29 + [300_000]
        t = compute_technicals("4751", _series(closes, volumes))
        assert t.volume_ratio_20d == 3.0

    def test_zero_volume_history(self):
        closes = [100.0] * 30
        volumes = [0] * 30
        t = compute_technicals("4751", _series(closes, volumes))
        assert t.volume_ratio_20d is None
