"""Unit tests for src/tools/edinet.py — EDINET XBRL adapter.

All tests run without an actual EDINET_API_KEY or network access.
edinet_tools.entity() and downstream calls are mocked.
"""
import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.tools import edinet
from src.tools.edinet import (
    _as_float,
    _find_raw,
    _has_api_key,
    _snake_to_pascal,
    get_line_items_for_ticker,
)


@pytest.fixture(autouse=True)
def _isolated_filings_dir(tmp_path, monkeypatch):
    """Point the persistent filings store at a temp dir so tests never touch data/filings."""
    monkeypatch.setenv("FILINGS_DIR", str(tmp_path / "filings"))


class TestHasApiKey:
    def test_unset(self, monkeypatch):
        monkeypatch.delenv("EDINET_API_KEY", raising=False)
        assert _has_api_key() is False

    def test_empty(self, monkeypatch):
        monkeypatch.setenv("EDINET_API_KEY", "")
        assert _has_api_key() is False

    def test_placeholder(self, monkeypatch):
        monkeypatch.setenv("EDINET_API_KEY", "your-edinet-api-key")
        assert _has_api_key() is False

    def test_real_value(self, monkeypatch):
        monkeypatch.setenv("EDINET_API_KEY", "abcd-1234-real-key")
        assert _has_api_key() is True


class TestAsFloat:
    def test_int(self):
        assert _as_float(123) == 123.0

    def test_float(self):
        assert _as_float(12.5) == 12.5

    def test_str_numeric(self):
        assert _as_float("123.45") == 123.45

    def test_str_with_value_dict(self):
        # XBRL elements sometimes arrive as {"value": "123", "unit": "JPY"}
        assert _as_float({"value": "12345"}) == 12345.0

    def test_none(self):
        assert _as_float(None) is None

    def test_garbage(self):
        assert _as_float("not a number") is None

    def test_empty_dict(self):
        assert _as_float({}) is None

    def test_list(self):
        # Lists are not coerceable
        assert _as_float([1, 2, 3]) is None


class TestSnakeToPascal:
    def test_basic(self):
        assert _snake_to_pascal("net_income") == "NetIncome"
        assert _snake_to_pascal("total_assets") == "TotalAssets"
        assert _snake_to_pascal("free_cash_flow") == "FreeCashFlow"

    def test_single_word(self):
        assert _snake_to_pascal("revenue") == "Revenue"

    def test_already_capitalized_first(self):
        assert _snake_to_pascal("Net_income") == "NetIncome"


class TestFindRaw:
    def _make_report(self, raw_fields):
        report = MagicMock()
        report.raw_fields = raw_fields
        return report

    def test_direct_match(self):
        report = self._make_report({"GrossProfit": 100_000_000})
        assert _find_raw(report, ["GrossProfit"]) == 100_000_000.0

    def test_substring_match_case_insensitive(self):
        report = self._make_report({"jpcrp_cor:GrossProfitIA": 50_000_000})
        # Case-insensitive substring should find it
        assert _find_raw(report, ["grossprofit"]) == 50_000_000.0

    def test_first_alias_wins(self):
        report = self._make_report({
            "PurchaseOfTreasuryShares": 1_000,
            "PurchaseOfTreasuryStock": 2_000,
        })
        # First alias is preferred
        result = _find_raw(report, ["PurchaseOfTreasuryShares", "PurchaseOfTreasuryStock"])
        assert result == 1_000.0

    def test_no_match_returns_none(self):
        report = self._make_report({"SomeOtherField": 999})
        assert _find_raw(report, ["NonExistent"]) is None

    def test_empty_raw_fields(self):
        report = self._make_report({})
        assert _find_raw(report, ["AnyField"]) is None

    def test_missing_raw_fields_attr(self):
        report = MagicMock(spec=[])  # no raw_fields attr
        report.raw_fields = None
        assert _find_raw(report, ["X"]) is None


class TestGetLineItemsForTickerNoApiKey:
    def test_returns_none_when_key_missing(self, monkeypatch):
        monkeypatch.delenv("EDINET_API_KEY", raising=False)
        edinet._fetch_latest_securities_report.cache_clear()
        result = get_line_items_for_ticker("4751", ["revenue"])
        assert result is None

    def test_returns_none_when_key_is_placeholder(self, monkeypatch):
        monkeypatch.setenv("EDINET_API_KEY", "your-edinet-api-key")
        edinet._fetch_latest_securities_report.cache_clear()
        result = get_line_items_for_ticker("4751", ["revenue"])
        assert result is None


class TestGetLineItemsForTickerWithMockedFetch:
    """Test the extraction logic with a fully mocked SecuritiesReport."""

    def _make_report(self):
        """Build a mock SecuritiesReport mimicking edinet-tools shape."""
        report = MagicMock()
        report.fiscal_year_end = date(2025, 9, 30)
        report.accounting_standard = "Japan GAAP"
        report.net_sales = 720_000_000_000
        report.net_income = 30_000_000_000
        report.total_assets = 850_000_000_000
        report.total_liabilities = 350_000_000_000
        report.net_assets = 500_000_000_000  # JP equivalent of shareholders_equity
        report.depreciation_amortization = 45_000_000_000
        report.operating_cash_flow = 80_000_000_000
        report.investing_cash_flow = -40_000_000_000  # negative = capex outflow
        report.raw_fields = {
            "GrossProfit": 230_000_000_000,
            "CapitalExpendituresIA": 38_000_000_000,
            "NumberOfIssuedSharesAtTheEndOfPeriodCommonStock": 524_000_000,
            "CashDividendsPaid": 8_000_000_000,
            "PurchaseOfTreasuryShares": 5_000_000_000,
        }
        return report

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_direct_fields(self, mock_fetch):
        mock_fetch.return_value = self._make_report()
        out = get_line_items_for_ticker(
            "4751",
            ["net_income", "revenue", "total_assets", "total_liabilities", "shareholders_equity",
             "depreciation_and_amortization"],
        )

        assert out["net_income"] == 30_000_000_000.0
        assert out["revenue"] == 720_000_000_000.0
        assert out["total_assets"] == 850_000_000_000.0
        assert out["total_liabilities"] == 350_000_000_000.0
        assert out["shareholders_equity"] == 500_000_000_000.0  # via net_assets
        assert out["depreciation_and_amortization"] == 45_000_000_000.0
        assert out["report_period"] == "2025-09-30"
        assert out["currency"] == "JPY"
        assert out["accounting_standard"] == "Japan GAAP"

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_free_cash_flow_computed(self, mock_fetch):
        mock_fetch.return_value = self._make_report()
        out = get_line_items_for_ticker("4751", ["free_cash_flow"])
        # JP convention: investing CF is already signed (negative outflow),
        # so FCF = OCF + ICF directly
        assert out["free_cash_flow"] == 80_000_000_000 + (-40_000_000_000)

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_raw_field_fallback(self, mock_fetch):
        mock_fetch.return_value = self._make_report()
        out = get_line_items_for_ticker(
            "4751",
            ["capital_expenditure", "gross_profit", "outstanding_shares",
             "dividends_and_other_cash_distributions", "issuance_or_purchase_of_equity_shares"],
        )
        assert out["capital_expenditure"] == 38_000_000_000.0
        assert out["gross_profit"] == 230_000_000_000.0
        assert out["outstanding_shares"] == 524_000_000.0
        assert out["dividends_and_other_cash_distributions"] == 8_000_000_000.0
        assert out["issuance_or_purchase_of_equity_shares"] == 5_000_000_000.0

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_unknown_item_returns_none(self, mock_fetch):
        mock_fetch.return_value = self._make_report()
        out = get_line_items_for_ticker("4751", ["totally_made_up_field"])
        assert out["totally_made_up_field"] is None

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_missing_ocf_or_icf_means_fcf_none(self, mock_fetch):
        report = self._make_report()
        report.operating_cash_flow = None
        mock_fetch.return_value = report
        out = get_line_items_for_ticker("4751", ["free_cash_flow"])
        assert out["free_cash_flow"] is None

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_returns_none_when_fetch_fails(self, mock_fetch):
        mock_fetch.return_value = None
        out = get_line_items_for_ticker("4751", ["revenue"])
        assert out is None


class TestPersistentCache:
    """Extracted line items survive across calls via filings_store (no re-fetch)."""

    def _make_report(self):
        report = MagicMock()
        report.fiscal_year_end = date(2025, 9, 30)
        report.accounting_standard = "Japan GAAP"
        report.net_sales = 720_000_000_000
        report.net_income = 30_000_000_000
        report.total_assets = None
        report.total_liabilities = None
        report.net_assets = None
        report.depreciation_amortization = None
        report.operating_cash_flow = None
        report.investing_cash_flow = None
        report.raw_fields = {}
        return report

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_second_call_served_from_store(self, mock_fetch):
        mock_fetch.return_value = self._make_report()

        out1 = get_line_items_for_ticker("4751", ["revenue", "net_income"])
        assert out1["revenue"] == 720_000_000_000.0
        assert mock_fetch.call_count == 1

        # Simulate a fresh process: fetch now fails, but the store answers
        mock_fetch.return_value = None
        out2 = get_line_items_for_ticker("4751", ["revenue", "net_income"])
        assert out2 is not None
        assert out2["revenue"] == 720_000_000_000.0
        assert out2["report_period"] == "2025-09-30"
        # No additional successful fetch was needed
        assert mock_fetch.call_count == 1

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_cache_miss_when_new_items_requested(self, mock_fetch):
        mock_fetch.return_value = self._make_report()
        get_line_items_for_ticker("4751", ["revenue"])
        assert mock_fetch.call_count == 1

        # Requesting an item not in the cached payload must re-fetch
        get_line_items_for_ticker("4751", ["revenue", "gross_profit"])
        assert mock_fetch.call_count == 2
