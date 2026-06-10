"""Unit tests for src/tools/screener.py — pure filter/scoring logic, no network."""
from datetime import date

from src.tools.screener import (
    apply_filters,
    build_report,
    dividend_yield_pct,
    effective_per,
    lot_filter,
    value_score,
)


class TestLotFilter:
    def test_pass_at_500k(self):
        # 500円 × 100株 = 50,000円 = 500kの10% → ちょうど通過
        assert lot_filter(500.0, 500_000) is True

    def test_fail_above(self):
        assert lot_filter(501.0, 500_000) is False
        # 4751 ケース: 1,316.5円 → 131,650円 = 26.3% NG
        assert lot_filter(1316.5, 500_000) is False

    def test_capital_scaling(self):
        # 総資本 ¥1.32M なら 1,316.5円 銘柄が通過 (watchlist の unlock 条件)
        assert lot_filter(1316.5, 1_320_000) is True

    def test_garbage(self):
        assert lot_filter(None, 500_000) is False
        assert lot_filter(0, 500_000) is False
        assert lot_filter(-10, 500_000) is False


class TestValueScore:
    def test_deep_value_scores_high(self):
        s = value_score({"per": 6.0, "pbr": 0.5, "dividend_yield_pct": 5.0})
        assert s == 100.0

    def test_expensive_scores_low(self):
        s = value_score({"per": 30.0, "pbr": 3.0, "dividend_yield_pct": 0.0})
        assert s == 0.0

    def test_negative_per_is_zero_component(self):
        # 赤字企業 (PER<0) はPER素点0だが他指標で救済余地
        s = value_score({"per": -5.0, "pbr": 0.5, "dividend_yield_pct": 4.5})
        assert s == round((0 + 100 + 100) / 3, 1)

    def test_partial_metrics(self):
        assert value_score({"per": 8.0}) == 100.0

    def test_no_metrics_is_none(self):
        assert value_score({}) is None
        assert value_score({"per": None, "pbr": None, "dividend_yield_pct": None}) is None


class TestEffectivePer:
    def test_pe_present_passthrough(self):
        assert effective_per({"trailingPE": 12.3}) == 12.3

    def test_loss_making_maps_to_negative(self):
        # 赤字企業: yfinance は trailingPE を返さない → EPS<0 で検知し PER=-1.0 (素点0)
        assert effective_per({"trailingPE": None, "trailingEps": -50.0}) == -1.0

    def test_no_data_is_none(self):
        assert effective_per({}) is None
        assert effective_per({"trailingPE": None, "trailingEps": None}) is None

    def test_positive_eps_without_pe_is_none(self):
        # EPS 正なのに PE 欠落 → 赤字とは判定しない
        assert effective_per({"trailingEps": 100.0}) is None


class TestDividendYieldPct:
    def test_rate_over_price_preferred(self):
        # dividendRate / price がバージョン非依存で最優先
        assert dividend_yield_pct({"dividendRate": 50.0, "dividendYield": 999.0}, 2000.0) == 2.5

    def test_trailing_rate_fallback(self):
        assert dividend_yield_pct({"trailingAnnualDividendRate": 30.0}, 1500.0) == 2.0

    def test_yield_fallback_is_already_percent(self):
        # yfinance 1.4.x は dividendYield をパーセント値で返す → ×100 してはいけない
        assert dividend_yield_pct({"dividendYield": 0.9}, None) == 0.9

    def test_low_yield_not_inflated(self):
        # 旧バグ: 0.9% が 90% に化けて低配当株が満点になっていた
        out = dividend_yield_pct({"dividendYield": 0.9}, None)
        assert out is not None and out < 5.0

    def test_no_data_is_none(self):
        assert dividend_yield_pct({}, 1000.0) is None


class TestApplyFilters:
    _ROWS = [
        {"ticker": "1111", "name": "割安A", "sector33": "卸売業", "price": 400.0,
         "per": 7.0, "pbr": 0.6, "dividend_yield_pct": 4.8, "market_cap": 50_000_000_000},
        {"ticker": "2222", "name": "割高B", "sector33": "情報・通信業", "price": 450.0,
         "per": 40.0, "pbr": 5.0, "dividend_yield_pct": 0.1, "market_cap": 60_000_000_000},
        {"ticker": "3333", "name": "高値C", "sector33": "卸売業", "price": 2000.0,
         "per": 5.0, "pbr": 0.4, "dividend_yield_pct": 5.0, "market_cap": 70_000_000_000},
        {"ticker": "4444", "name": "小型D", "sector33": "サービス業", "price": 300.0,
         "per": 6.0, "pbr": 0.5, "dividend_yield_pct": 4.0, "market_cap": 5_000_000_000},
        {"ticker": "5555", "name": "指標欠落E", "sector33": "サービス業", "price": 300.0,
         "per": None, "pbr": None, "dividend_yield_pct": None, "market_cap": 50_000_000_000},
    ]

    def test_filters_and_ranking(self):
        out = apply_filters(self._ROWS, capital=500_000, market_cap_min=10_000_000_000)
        tickers = [r["ticker"] for r in out]
        assert "3333" not in tickers  # lot filter (2,000円 > 500円)
        assert "4444" not in tickers  # market cap < 100億
        assert "5555" not in tickers  # 指標なし
        assert tickers[0] == "1111"   # 割安Aが割高Bより上位
        assert out[0]["value_score"] > out[-1]["value_score"]

    def test_unknown_market_cap_passes(self):
        rows = [{"ticker": "6666", "name": "MC不明", "sector33": "x", "price": 400.0,
                 "per": 10.0, "pbr": 1.0, "dividend_yield_pct": 3.0, "market_cap": None}]
        out = apply_filters(rows, capital=500_000, market_cap_min=10_000_000_000)
        assert len(out) == 1


class TestBuildReport:
    def test_report_shape(self):
        cands = apply_filters(TestApplyFilters._ROWS, 500_000, 10_000_000_000)
        rep = build_report(cands, 500_000, top=10, today=date(2026, 6, 10), universe_size=1000)
        assert "割安株スクリーニング — 2026-06-10" in rep
        assert "推奨ではない" in rep
        assert "| 1 | 1111 |" in rep
