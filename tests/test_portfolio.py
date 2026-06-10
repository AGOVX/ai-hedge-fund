"""Unit tests for src/tools/portfolio.py — file IO isolated via env paths."""
import math

import pytest

from src.tools import portfolio
from src.tools.portfolio import (
    add_position,
    correlation_matrix,
    load_portfolio,
    sector_concentration,
    value_positions,
)


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTFOLIO_PATH", str(tmp_path / "current.yaml"))
    monkeypatch.setenv("EQUITY_CURVE_PATH", str(tmp_path / "equity-curve.csv"))


class TestApprovalGuard:
    def test_add_without_approval_raises(self):
        with pytest.raises(ValueError, match="株主承認"):
            add_position("8001", "伊藤忠", 100, 2050.0, "REC-X", "druckenmiller",
                         approved_by_shareholder=False)

    def test_add_with_approval(self):
        add_position("8001", "伊藤忠", 100, 2050.0, "REC-X", "druckenmiller",
                     approved_by_shareholder=True)
        data = load_portfolio()
        assert len(data["positions"]) == 1
        assert data["positions"][0]["approved_by_shareholder"] is True

    def test_unapproved_entries_rejected_on_load(self, tmp_path):
        (tmp_path / "current.yaml").write_text(
            "capital_assumption_jpy: 500000\n"
            "positions:\n"
            "  - {ticker: '9999', name: bad, shares: 100, entry_price: 100,"
            " approved_by_shareholder: false}\n",
            encoding="utf-8",
        )
        data = load_portfolio()
        assert data["positions"] == []

    def test_duplicate_ticker_rejected(self):
        add_position("8001", "伊藤忠", 100, 2050.0, "REC-X", "druckenmiller", True)
        with pytest.raises(ValueError, match="既に建玉"):
            add_position("8001", "伊藤忠", 100, 2100.0, "REC-Y", "buffett", True)

    def test_invalid_quantities(self):
        with pytest.raises(ValueError):
            add_position("8001", "x", 0, 2050.0, "REC-X", "pm", True)
        with pytest.raises(ValueError):
            add_position("8001", "x", 100, -1.0, "REC-X", "pm", True)


class TestValuation:
    def _data(self):
        return {
            "capital_assumption_jpy": 500_000,
            "positions": [
                {"ticker": "8001", "name": "伊藤忠", "shares": 100, "entry_price": 2050.0,
                 "approved_by_shareholder": True, "pm_sponsor": "druckenmiller",
                 "sector": "卸売業"},
            ],
        }

    def test_mark_to_market(self):
        v = value_positions(self._data(), {"8001": 2200.0})
        assert v["positions_value_jpy"] == 220_000
        assert v["cash_jpy"] == 500_000 - 205_000
        assert v["equity_jpy"] == 295_000 + 220_000
        assert v["unrealized_pl_jpy"] == 15_000
        assert v["rows"][0]["unrealized_pl_pct"] == pytest.approx(7.32, abs=0.01)

    def test_missing_price_falls_back_to_entry(self):
        v = value_positions(self._data(), {})
        assert v["unrealized_pl_jpy"] == 0

    def test_invested_ratio(self):
        v = value_positions(self._data(), {"8001": 2050.0})
        assert v["invested_ratio_pct"] == 41.0  # 205,000 / 500,000


class TestConcentration:
    def test_sector_weights(self):
        rows = [
            {"value_jpy": 300_000, "sector": "卸売業"},
            {"value_jpy": 100_000, "sector": "情報・通信業"},
            {"value_jpy": 100_000},  # sector 未記載
        ]
        out = sector_concentration(rows)
        assert out["卸売業"] == 60.0
        assert out["unknown"] == 20.0


class TestCorrelation:
    def test_perfect_correlation(self):
        r = [0.01 * math.sin(i) for i in range(50)]
        out = correlation_matrix({"A": r, "B": list(r)})
        assert out[("A", "B")] == 1.0

    def test_anticorrelation(self):
        r = [0.01 * math.sin(i) for i in range(50)]
        out = correlation_matrix({"A": r, "B": [-x for x in r]})
        assert out[("A", "B")] == -1.0

    def test_single_ticker_empty(self):
        assert correlation_matrix({"A": [0.01] * 50}) == {}

    def test_short_series_skipped(self):
        assert correlation_matrix({"A": [0.01] * 5, "B": [0.02] * 5}) == {}

    def test_zero_variance_skipped(self):
        flat = [0.0] * 50
        wavy = [0.01 * math.sin(i) for i in range(50)]
        assert correlation_matrix({"A": flat, "B": wavy}) == {}
