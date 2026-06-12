"""Unit tests for src/tools/portfolio.py — file IO isolated via env paths."""
import math

import pytest

from src.tools import portfolio
from src.tools.portfolio import (
    add_position,
    aligned_returns,
    append_equity_snapshot,
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

    def test_unapproved_entries_survive_save_roundtrip(self, tmp_path):
        # 旧バグ: load で弾いた未承認建玉が save で無言削除されていた
        (tmp_path / "current.yaml").write_text(
            "capital_assumption_jpy: 500000\n"
            "positions:\n"
            "  - {ticker: '9999', name: bad, shares: 100, entry_price: 100,"
            " approved_by_shareholder: false}\n",
            encoding="utf-8",
        )
        add_position("8001", "伊藤忠", 100, 2050.0, "REC-X", "druckenmiller", True)
        raw = (tmp_path / "current.yaml").read_text(encoding="utf-8")
        assert "'9999'" in raw or "9999" in raw  # 未承認分が YAML に残る
        assert "_unapproved_positions" not in raw  # 内部キーは書き出さない
        data = load_portfolio()
        assert [p["ticker"] for p in data["positions"]] == ["8001"]
        assert [p["ticker"] for p in data["_unapproved_positions"]] == ["9999"]

    def test_duplicate_check_includes_unapproved(self, tmp_path):
        (tmp_path / "current.yaml").write_text(
            "positions:\n"
            "  - {ticker: '9999', name: bad, shares: 100, entry_price: 100,"
            " approved_by_shareholder: false}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="既に建玉"):
            add_position("9999", "dup", 100, 100.0, "REC-X", "pm", True)

    def test_sector_saved(self):
        add_position("8001", "伊藤忠", 100, 2050.0, "REC-X", "druckenmiller", True,
                     sector="卸売業")
        data = load_portfolio()
        assert data["positions"][0]["sector"] == "卸売業"


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


class TestEquityCurve:
    _VAL = {"equity_jpy": 500_000, "cash_jpy": 500_000,
            "positions_value_jpy": 0, "unrealized_pl_jpy": 0}

    def test_same_day_rerun_replaces_row(self, tmp_path):
        # 定期実行 + 手動実行が同日に重なっても1行に保つ
        append_equity_snapshot(self._VAL, 410.2, note="run1")
        append_equity_snapshot(self._VAL, 410.6, note="run2")
        lines = (tmp_path / "equity-curve.csv").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2  # header + 1行
        assert "run2" in lines[1] and "410.6" in lines[1]

    def test_past_rows_preserved(self, tmp_path):
        (tmp_path / "equity-curve.csv").write_text(
            "date,equity_jpy,cash_jpy,positions_value_jpy,"
            "realized_pl_cum_jpy,unrealized_pl_jpy,topix_close,note\n"
            "2026-05-12,500000,500000,0,0,0,,initial baseline\n",
            encoding="utf-8",
        )
        append_equity_snapshot(self._VAL, None, note="today")
        lines = (tmp_path / "equity-curve.csv").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        assert "2026-05-12" in lines[1]


class TestAlignedReturns:
    def test_common_dates_only(self):
        # B に欠損日 (01-02) があっても共通日付の積集合上でリターンを取る
        a = [("2026-01-01", 100.0), ("2026-01-02", 110.0), ("2026-01-03", 121.0)]
        b = [("2026-01-01", 200.0), ("2026-01-03", 220.0)]
        out = aligned_returns({"A": a, "B": b})
        assert out["A"] == pytest.approx([0.21])   # 100 → 121 (01-02 を飛ばす)
        assert out["B"] == pytest.approx([0.10])   # 200 → 220
        assert len(out["A"]) == len(out["B"])

    def test_same_dates_normal_returns(self):
        a = [("2026-01-01", 100.0), ("2026-01-02", 102.0)]
        out = aligned_returns({"A": a})
        assert out["A"] == pytest.approx([0.02])

    def test_empty(self):
        assert aligned_returns({}) == {}


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
