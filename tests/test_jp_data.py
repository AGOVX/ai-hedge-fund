"""Unit tests for src/tools/jp_data.py — JP ticker adapter via yfinance.

Pure-function tests + mocked yfinance calls (no network).
"""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.data.models import Price, FinancialMetrics
from src.tools.jp_data import (
    get_company_news_jp,
    get_financial_metrics_jp,
    get_insider_trades_jp,
    get_market_cap_jp,
    get_prices_jp,
    is_jp_ticker,
    search_line_items_jp,
    to_yfinance_symbol,
)


class TestIsJpTicker:
    def test_4_digit_numeric_is_jp(self):
        assert is_jp_ticker("4751") is True
        assert is_jp_ticker("8001") is True
        assert is_jp_ticker("7203") is True
        assert is_jp_ticker("0001") is True  # leading zero allowed

    def test_us_alpha_is_not_jp(self):
        assert is_jp_ticker("AAPL") is False
        assert is_jp_ticker("MSFT") is False
        assert is_jp_ticker("BRK.B") is False

    def test_wrong_digit_count_is_not_jp(self):
        assert is_jp_ticker("123") is False        # 3 digits
        assert is_jp_ticker("12345") is False      # 5 digits
        assert is_jp_ticker("") is False           # empty

    def test_mixed_alphanumeric_is_not_jp(self):
        assert is_jp_ticker("475A") is False
        assert is_jp_ticker("A751") is False


class TestToYfinanceSymbol:
    def test_appends_dot_t_suffix(self):
        assert to_yfinance_symbol("4751") == "4751.T"
        assert to_yfinance_symbol("8001") == "8001.T"


class TestGetPricesJp:
    def _make_yf_df(self, dates=None):
        if dates is None:
            dates = pd.date_range("2026-04-01", periods=3, freq="D")
        return pd.DataFrame(
            {
                "Open": [1000.0, 1010.0, 1020.0],
                "Close": [1005.0, 1015.0, 1025.0],
                "High": [1015.0, 1020.0, 1030.0],
                "Low": [995.0, 1005.0, 1015.0],
                "Volume": [100000, 110000, 120000],
            },
            index=dates,
        )

    @patch("yfinance.Ticker")
    def test_returns_price_objects(self, mock_ticker_class):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = self._make_yf_df()
        mock_ticker_class.return_value = mock_ticker

        prices = get_prices_jp("4751", "2026-04-01", "2026-04-03")

        assert len(prices) == 3
        assert all(isinstance(p, Price) for p in prices)
        assert prices[0].open == 1000.0
        assert prices[0].close == 1005.0
        assert prices[0].volume == 100000
        # Verify yfinance called with the correct symbol
        mock_ticker_class.assert_called_once_with("4751.T")

    @patch("yfinance.Ticker")
    def test_empty_df_returns_empty_list(self, mock_ticker_class):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_class.return_value = mock_ticker

        prices = get_prices_jp("4751", "2026-04-01", "2026-04-03")
        assert prices == []

    @patch("yfinance.Ticker")
    def test_yfinance_exception_returns_empty_list(self, mock_ticker_class):
        mock_ticker = MagicMock()
        mock_ticker.history.side_effect = ConnectionError("network down")
        mock_ticker_class.return_value = mock_ticker

        prices = get_prices_jp("4751", "2026-04-01", "2026-04-03")
        assert prices == []


class TestGetMarketCapJp:
    @patch("yfinance.Ticker")
    def test_returns_float_when_present(self, mock_ticker_class):
        mock_ticker = MagicMock()
        mock_ticker.info = {"marketCap": 690_000_000_000}
        mock_ticker_class.return_value = mock_ticker

        mc = get_market_cap_jp("4751", "2026-05-15")
        assert mc == 690_000_000_000.0
        assert isinstance(mc, float)
        mock_ticker_class.assert_called_once_with("4751.T")

    @patch("yfinance.Ticker")
    def test_missing_key_returns_none(self, mock_ticker_class):
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker_class.return_value = mock_ticker

        assert get_market_cap_jp("4751", "2026-05-15") is None

    @patch("yfinance.Ticker")
    def test_exception_returns_none(self, mock_ticker_class):
        mock_ticker_class.side_effect = RuntimeError("API error")

        assert get_market_cap_jp("4751", "2026-05-15") is None


class TestGetFinancialMetricsJp:
    def _make_info(self):
        return {
            "currency": "JPY",
            "marketCap": 690_000_000_000,
            "trailingPE": 16.96,
            "priceToBook": 3.48,
            "returnOnEquity": 0.211,
            "debtToEquity": 33.8,  # yfinance returns in PERCENT
            "operatingMargins": 0.118,
            "grossMargins": 0.321,
            "profitMargins": 0.046,
            "currentRatio": 1.7,
            "freeCashflow": 33_000_000_000,
            "sharesOutstanding": 524_000_000,
            "trailingEps": 60.0,
            "bookValue": 378.0,
        }

    @patch("yfinance.Ticker")
    def test_debt_to_equity_normalized_to_ratio(self, mock_ticker_class):
        """yfinance gives D/E in % (33.8), we must divide by 100."""
        mock_ticker = MagicMock()
        mock_ticker.info = self._make_info()
        mock_ticker_class.return_value = mock_ticker

        metrics = get_financial_metrics_jp("4751", "2026-05-15", limit=1)
        assert len(metrics) == 1
        m = metrics[0]
        assert isinstance(m, FinancialMetrics)
        assert m.debt_to_equity == pytest.approx(0.338, rel=1e-3)
        # Buffett threshold: < 0.5 should pass
        assert m.debt_to_equity < 0.5

    @patch("yfinance.Ticker")
    def test_margins_stay_as_fractions(self, mock_ticker_class):
        """ROE, op_margin etc. are already 0-1 fractions in yfinance."""
        mock_ticker = MagicMock()
        mock_ticker.info = self._make_info()
        mock_ticker_class.return_value = mock_ticker

        m = get_financial_metrics_jp("4751", "2026-05-15", limit=1)[0]
        assert m.return_on_equity == pytest.approx(0.211)
        assert m.operating_margin == pytest.approx(0.118)
        assert m.gross_margin == pytest.approx(0.321)
        assert m.net_margin == pytest.approx(0.046)

    @patch("yfinance.Ticker")
    def test_fcf_yield_computed(self, mock_ticker_class):
        """fcf_yield = freeCashflow / marketCap when both present."""
        mock_ticker = MagicMock()
        mock_ticker.info = self._make_info()
        mock_ticker_class.return_value = mock_ticker

        m = get_financial_metrics_jp("4751", "2026-05-15", limit=1)[0]
        expected = 33_000_000_000 / 690_000_000_000
        assert m.free_cash_flow_yield == pytest.approx(expected)

    @patch("yfinance.Ticker")
    def test_limit_duplicates_snapshot(self, mock_ticker_class):
        mock_ticker = MagicMock()
        mock_ticker.info = self._make_info()
        mock_ticker_class.return_value = mock_ticker

        metrics = get_financial_metrics_jp("4751", "2026-05-15", limit=5)
        assert len(metrics) == 5
        # All snapshots reference the same underlying dict (Phase 2.1 limitation)
        assert all(m.market_cap == 690_000_000_000.0 for m in metrics)

    @patch("yfinance.Ticker")
    def test_empty_info_returns_empty(self, mock_ticker_class):
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker_class.return_value = mock_ticker

        assert get_financial_metrics_jp("4751", "2026-05-15") == []

    @patch("yfinance.Ticker")
    def test_default_currency_is_jpy(self, mock_ticker_class):
        mock_ticker = MagicMock()
        mock_ticker.info = {"marketCap": 1.0}  # no currency key
        mock_ticker_class.return_value = mock_ticker

        m = get_financial_metrics_jp("4751", "2026-05-15", limit=1)[0]
        assert m.currency == "JPY"


class TestStubs:
    """Phase 2.3-2.5 stubs — should return [] without raising."""

    def test_search_line_items_jp_returns_empty(self, caplog):
        # When EDINET_API_KEY is not set the function returns [] silently
        # (the warning is logged by edinet.py, not jp_data.py).
        # We don't assert on the log message here to keep this test independent
        # of edinet.py's logging behavior.
        result = search_line_items_jp("4751", ["revenue"], "2026-05-15")
        assert result == []

    def test_get_insider_trades_jp_returns_empty(self):
        result = get_insider_trades_jp("4751", "2026-05-15")
        assert result == []

    def test_get_company_news_jp_returns_empty(self):
        result = get_company_news_jp("4751", "2026-05-15")
        assert result == []
