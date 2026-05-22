"""Unit tests for src/tools/api.py routing logic.

Verifies that JP tickers (4-digit) are routed to jp_data.py adapters
and US tickers (alphabetic) still hit the financialdatasets.ai code path.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.tools import api
from src.tools.api import (
    get_company_news,
    get_financial_metrics,
    get_insider_trades,
    get_market_cap,
    get_prices,
    search_line_items,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Reset the in-memory cache between tests so routing decisions aren't masked."""
    api._cache = type(api._cache)()
    yield


# ----------------- get_prices -----------------

class TestGetPricesRouting:
    @patch("src.tools.api.get_prices_jp")
    def test_jp_ticker_routes_to_jp_adapter(self, mock_jp):
        mock_jp.return_value = []
        get_prices("4751", "2026-04-01", "2026-05-15")
        mock_jp.assert_called_once_with("4751", "2026-04-01", "2026-05-15")

    @patch("src.tools.api.get_prices_jp")
    @patch("src.tools.api._make_api_request")
    def test_us_ticker_does_NOT_call_jp_adapter(self, mock_http, mock_jp):
        mock_http.return_value = MagicMock(status_code=200, json=lambda: {"ticker": "AAPL", "prices": []})
        get_prices("AAPL", "2026-04-01", "2026-05-15")
        mock_jp.assert_not_called()
        mock_http.assert_called_once()


# ----------------- get_financial_metrics -----------------

class TestGetFinancialMetricsRouting:
    @patch("src.tools.api.get_financial_metrics_jp")
    def test_jp_ticker_routes_to_jp(self, mock_jp):
        mock_jp.return_value = []
        get_financial_metrics("8001", "2026-05-15", period="ttm", limit=5)
        mock_jp.assert_called_once_with("8001", "2026-05-15", period="ttm", limit=5)

    @patch("src.tools.api.get_financial_metrics_jp")
    @patch("src.tools.api._make_api_request")
    def test_us_ticker_skips_jp(self, mock_http, mock_jp):
        mock_http.return_value = MagicMock(
            status_code=200,
            json=lambda: {"financial_metrics": []},
        )
        get_financial_metrics("MSFT", "2026-05-15", period="ttm", limit=5)
        mock_jp.assert_not_called()


# ----------------- get_market_cap -----------------

class TestGetMarketCapRouting:
    @patch("src.tools.api.get_market_cap_jp")
    def test_jp_ticker_routes_to_jp(self, mock_jp):
        mock_jp.return_value = 1_000_000.0
        result = get_market_cap("4751", "2026-05-15")
        mock_jp.assert_called_once_with("4751", "2026-05-15")
        assert result == 1_000_000.0

    @patch("src.tools.api.get_market_cap_jp")
    def test_us_ticker_skips_jp(self, mock_jp):
        # We don't need to mock the US path fully — just verify jp_adapter not called.
        # The US path will likely fail with no API key, but that's irrelevant here.
        try:
            get_market_cap("AAPL", "2026-05-15")
        except Exception:
            pass
        mock_jp.assert_not_called()


# ----------------- search_line_items -----------------

class TestSearchLineItemsRouting:
    @patch("src.tools.api.search_line_items_jp")
    def test_jp_ticker_routes_to_jp(self, mock_jp):
        mock_jp.return_value = []
        search_line_items("4751", ["revenue"], "2026-05-15", period="ttm", limit=10)
        mock_jp.assert_called_once_with("4751", ["revenue"], "2026-05-15", period="ttm", limit=10)

    @patch("src.tools.api.search_line_items_jp")
    @patch("src.tools.api._make_api_request")
    def test_us_ticker_skips_jp(self, mock_http, mock_jp):
        mock_http.return_value = MagicMock(
            status_code=200,
            json=lambda: {"search_results": []},
        )
        search_line_items("AAPL", ["revenue"], "2026-05-15")
        mock_jp.assert_not_called()


# ----------------- get_insider_trades -----------------

class TestGetInsiderTradesRouting:
    @patch("src.tools.api.get_insider_trades_jp")
    def test_jp_ticker_routes_to_jp(self, mock_jp):
        mock_jp.return_value = []
        get_insider_trades("4751", "2026-05-15", start_date="2026-01-01", limit=500)
        mock_jp.assert_called_once_with("4751", "2026-05-15", start_date="2026-01-01", limit=500)


# ----------------- get_company_news -----------------

class TestGetCompanyNewsRouting:
    @patch("src.tools.api.get_company_news_jp")
    def test_jp_ticker_routes_to_jp(self, mock_jp):
        mock_jp.return_value = []
        get_company_news("4751", "2026-05-15", start_date="2026-01-01", limit=200)
        mock_jp.assert_called_once_with("4751", "2026-05-15", start_date="2026-01-01", limit=200)


# ----------------- Regression: existing US path still cacheable -----------------

class TestUsCachingStillWorks:
    """Smoke-test that US path doesn't accidentally bypass cache after JP routing was added."""

    @patch("src.tools.api._make_api_request")
    def test_us_prices_cached_on_second_call(self, mock_http):
        # First call: hits HTTP
        mock_http.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "ticker": "AAPL",
                "prices": [
                    {"open": 100.0, "close": 101.0, "high": 102.0, "low": 99.0,
                     "volume": 1000000, "time": "2026-04-01T00:00:00"},
                ],
            },
        )
        first = get_prices("AAPL", "2026-04-01", "2026-04-02")
        assert len(first) == 1

        # Second call with same args: should be served from cache (no HTTP)
        mock_http.reset_mock()
        second = get_prices("AAPL", "2026-04-01", "2026-04-02")
        assert len(second) == 1
        mock_http.assert_not_called()
