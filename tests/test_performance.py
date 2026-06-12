"""Unit tests for src/tools/performance.py — pure metrics, no network."""
import math
from datetime import date

import pytest

from src.tools.performance import (
    annualized_return_pct,
    build_report,
    cumulative_return_pct,
    excess_vs_benchmark_pct,
    load_equity_curve,
    max_drawdown_pct,
    pm_breakdown,
    returns_from_values,
    sharpe_ratio,
    sortino_ratio,
)

TODAY = date(2026, 6, 10)


class TestReturns:
    def test_simple(self):
        assert returns_from_values([100.0, 110.0, 99.0]) == pytest.approx([0.10, -0.10])

    def test_cumulative(self):
        assert cumulative_return_pct([500_000, 550_000]) == pytest.approx(10.0)

    def test_too_short(self):
        assert cumulative_return_pct([500_000]) is None
        assert returns_from_values([100.0]) == []

    def test_annualized_one_year(self):
        out = annualized_return_pct([100.0, 110.0], ["2025-06-10", "2026-06-10"])
        assert out == pytest.approx(10.0, abs=0.2)

    def test_annualized_same_day_none(self):
        assert annualized_return_pct([100.0, 110.0], ["2026-06-10", "2026-06-10"]) is None


class TestRiskRatios:
    def test_sharpe_positive_drift(self):
        rets = [0.01, 0.02, 0.01, 0.015, 0.005]
        s = sharpe_ratio(rets)
        assert s is not None and s > 0

    def test_sharpe_zero_variance_none(self):
        assert sharpe_ratio([0.01] * 10) is None

    def test_sharpe_too_short(self):
        assert sharpe_ratio([0.01]) is None

    def test_sortino_no_downside_none(self):
        # マイナスリターンが1つも無い場合は ∞ を返さず None
        assert sortino_ratio([0.01, 0.02, 0.03]) is None

    def test_sortino_with_downside(self):
        s = sortino_ratio([0.02, -0.01, 0.03, -0.02, 0.01])
        assert s is not None and not math.isnan(s)

    def test_max_drawdown(self):
        # 100 → 120 → 90: ピーク120から90 = -25%
        assert max_drawdown_pct([100.0, 120.0, 90.0, 110.0]) == pytest.approx(-25.0)

    def test_max_drawdown_monotonic_zero(self):
        assert max_drawdown_pct([100.0, 110.0, 120.0]) == 0.0


class TestBenchmark:
    def test_excess(self):
        # ポート +10% vs ベンチ +4% → +6pt
        out = excess_vs_benchmark_pct([100.0, 110.0], [50.0, 52.0])
        assert out == pytest.approx(6.0)

    def test_missing_none(self):
        assert excess_vs_benchmark_pct([100.0], [50.0, 52.0]) is None


class TestPmBreakdown:
    def test_groups(self):
        rows = [
            {"pm_sponsor": "buffett", "value_jpy": 100_000, "unrealized_pl_jpy": 5_000},
            {"pm_sponsor": "buffett", "value_jpy": 50_000, "unrealized_pl_jpy": -1_000},
            {"pm_sponsor": "dalio", "value_jpy": 80_000, "unrealized_pl_jpy": 0},
        ]
        out = pm_breakdown(rows)
        assert out["buffett"]["positions"] == 2
        assert out["buffett"]["unrealized_pl_jpy"] == 4_000
        assert out["dalio"]["value_jpy"] == 80_000

    def test_missing_pm_is_unknown(self):
        out = pm_breakdown([{"value_jpy": 10_000, "unrealized_pl_jpy": 0}])
        assert "unknown" in out


class TestLoadCurve:
    def test_loads_and_sorts(self, tmp_path, monkeypatch):
        p = tmp_path / "equity-curve.csv"
        p.write_text(
            "date,equity_jpy,cash_jpy,positions_value_jpy,"
            "realized_pl_cum_jpy,unrealized_pl_jpy,topix_close,note\n"
            "2026-06-10,510000,510000,0,0,0,410.6,b\n"
            "2026-05-12,500000,500000,0,0,0,,a\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("EQUITY_CURVE_PATH", str(p))
        curve = load_equity_curve()
        assert [r["date"] for r in curve] == ["2026-05-12", "2026-06-10"]
        assert curve[0]["topix_close"] is None
        assert curve[1]["equity_jpy"] == 510_000

    def test_bom_tolerated(self, tmp_path, monkeypatch):
        p = tmp_path / "equity-curve.csv"
        p.write_text(
            "﻿date,equity_jpy,cash_jpy,positions_value_jpy,"
            "realized_pl_cum_jpy,unrealized_pl_jpy,topix_close,note\n"
            "2026-05-12,500000,500000,0,0,0,,a\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("EQUITY_CURVE_PATH", str(p))
        assert len(load_equity_curve()) == 1

    def test_missing_file_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EQUITY_CURVE_PATH", str(tmp_path / "nope.csv"))
        assert load_equity_curve() == []


class TestBuildReport:
    def test_insufficient_data(self):
        rep = build_report([{"date": "2026-05-12", "equity_jpy": 500_000, "topix_close": None}], {}, TODAY)
        assert "データ不足" in rep

    def test_small_n_caveat(self):
        curve = [
            {"date": "2026-05-12", "equity_jpy": 500_000.0, "topix_close": 400.0},
            {"date": "2026-06-10", "equity_jpy": 510_000.0, "topix_close": 410.0},
        ]
        rep = build_report(curve, {}, TODAY)
        assert "参考値" in rep
        assert "+2.00%" in rep          # 累積リターン
        assert "建玉なし" in rep
        assert "推奨ではない" in rep

    def test_pm_table(self):
        curve = [
            {"date": "2026-05-12", "equity_jpy": 500_000.0, "topix_close": None},
            {"date": "2026-06-10", "equity_jpy": 510_000.0, "topix_close": None},
        ]
        pm = {"druckenmiller": {"positions": 1, "value_jpy": 220_000.0, "unrealized_pl_jpy": 15_000.0}}
        rep = build_report(curve, pm, TODAY)
        assert "| druckenmiller | 1 |" in rep
        assert "¥+15,000" in rep
