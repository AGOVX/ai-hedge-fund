"""Unit tests for src/tools/edinet.py — EDINET XBRL adapter.

All tests run without an actual EDINET_API_KEY or network access.
edinet_tools.entity() and downstream calls are mocked.
"""
import os
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.tools import edinet, filings_store
from src.tools.edinet import (
    _as_float,
    _extract_interim,
    _find_raw,
    _has_api_key,
    _snake_to_pascal,
    build_history_report,
    build_interim_report,
    get_line_items_for_ticker,
    get_line_items_history,
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


class TestLineItemsHistory:
    """過去期の有報時系列 — キャッシュ/マーカー優先でネットワークを叩かないこと。"""

    def _seed(self, periods: list[str]):
        for i, period in enumerate(periods):
            filings_store.save_line_items(
                "8001",
                f"DOC{i}",
                {"net_income": 100.0 + i, "shareholders_equity": 1000.0,
                 "report_period": period},
            )

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_served_from_cache_when_enough_periods(self, mock_fetch):
        self._seed(["2024-03-31", "2025-03-31", "2026-03-31"])
        out = get_line_items_history("8001", ["net_income", "shareholders_equity"], periods=3)
        assert [p["report_period"] for p in out] == ["2026-03-31", "2025-03-31", "2024-03-31"]
        mock_fetch.assert_not_called()

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_marker_prevents_rescan_for_short_history(self, mock_fetch):
        # 上場3年の銘柄: 3期しか無いが走査済みマーカーがあれば再走査しない
        self._seed(["2024-03-31", "2025-03-31", "2026-03-31"])
        filings_store.save_line_items(
            "8001", "__history_scan__",
            {"scanned_periods": 10, "found": 2, "report_period": None},
        )
        out = get_line_items_history("8001", ["net_income", "shareholders_equity"], periods=10)
        assert len(out) == 3
        mock_fetch.assert_not_called()

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_degrades_to_cache_when_fetch_fails(self, mock_fetch):
        mock_fetch.return_value = None
        self._seed(["2025-03-31", "2026-03-31"])
        edinet._fetch_latest_securities_report.cache_clear()
        out = get_line_items_history("8001", ["net_income", "shareholders_equity"], periods=10)
        assert len(out) == 2  # ネットワーク不可でもキャッシュ分は返す

    @patch("src.tools.edinet._fetch_latest_securities_report")
    def test_no_key_no_cache_empty(self, mock_fetch, monkeypatch):
        monkeypatch.delenv("EDINET_API_KEY", raising=False)
        mock_fetch.return_value = None
        edinet._fetch_latest_securities_report.cache_clear()
        assert get_line_items_history("9999", ["net_income"], periods=10) == []


class TestBuildHistoryReport:
    def test_roe_and_table(self):
        payloads = [
            {"report_period": "2026-03-31", "revenue": 1_000e8, "net_income": 80e8,
             "shareholders_equity": 800e8, "total_assets": 2_000e8, "free_cash_flow": 60e8},
            {"report_period": "2025-03-31", "revenue": 900e8, "net_income": 72e8,
             "shareholders_equity": 720e8, "total_assets": 1_800e8, "free_cash_flow": None},
        ]
        rep = build_history_report("8001", payloads)
        assert "2 期分" in rep
        assert "| 2026-03-31 | 1,000 | 80 | 8.0% | 10.0% | 40.0% | 60 |" in rep
        assert "| 2025-03-31 |" in rep and "| — |" in rep  # FCF 欠損は —
        assert "ROE: 平均 10.0%" in rep

    def test_missing_values_safe(self):
        rep = build_history_report("8001", [{"report_period": "2026-03-31"}])
        assert "—" in rep
        assert "ROE: 平均" not in rep  # ROE 計算不能なら集計行なし


class _FakeFact:
    """raw_facts の Fact (element_id / context_id / value) を模す。"""
    def __init__(self, element_id, context_id, value):
        self.element_id = element_id
        self.context_id = context_id
        self.value = value


class _FakeInterimReport:
    """半期報告書 parse() 結果の最小モック (4751 FY2026 上期の実値ベース)。"""
    doc_type_code = "160"

    def __init__(self):
        self.period_start = date(2025, 10, 1)
        self.period_end = date(2026, 3, 31)
        self.raw_fields = {
            "jpdei_cor:TypeOfCurrentPeriodDEI": "HY",
            "jpdei_cor:CurrentFiscalYearEndDateDEI": "2026-09-30",
            "jpdei_cor:ComparativePeriodEndDateDEI": "2025-03-31",
        }
        self.raw_facts = [
            # 当中間期 (連結, InterimDuration)
            _FakeFact("jppfs_cor:NetSales", "InterimDuration", 478584000000),
            _FakeFact("jppfs_cor:OperatingIncome", "InterimDuration", 52459000000),
            _FakeFact("jppfs_cor:ProfitLossAttributableToOwnersOfParent", "InterimDuration", 27336000000),
            _FakeFact("jppfs_cor:ProfitLoss", "InterimDuration", 35584000000),  # 親株主版を優先すべき
            _FakeFact("jppfs_cor:NetCashProvidedByUsedInOperatingActivities", "InterimDuration", 19612000000),
            _FakeFact("jppfs_cor:NetCashProvidedByUsedInInvestmentActivities", "InterimDuration", -48104000000),
            # セグメント別内訳 (_接尾辞) — 拾ってはいけない
            _FakeFact("jppfs_cor:NetSales", "InterimDuration_GameSegmentMember", 132227000000),
            # 当期末BS (連結, InterimInstant)
            _FakeFact("jppfs_cor:Assets", "InterimInstant", 556509000000),
            _FakeFact("jppfs_cor:NetAssets", "InterimInstant", 284384000000),
            # 前期末BS — 拾ってはいけない
            _FakeFact("jppfs_cor:Assets", "Prior1YearInstant", 557162000000),
            _FakeFact("jppfs_cor:NetAssets", "Prior1YearInstant", 275681000000),
            # 前年同期 (Prior1InterimDuration)
            _FakeFact("jppfs_cor:NetSales", "Prior1InterimDuration", 421214000000),
            _FakeFact("jppfs_cor:OperatingIncome", "Prior1InterimDuration", 29169000000),
            _FakeFact("jppfs_cor:ProfitLossAttributableToOwnersOfParent", "Prior1InterimDuration", 15863000000),
            _FakeFact("jppfs_cor:NetCashProvidedByUsedInOperatingActivities", "Prior1InterimDuration", 23786000000),
            _FakeFact("jppfs_cor:NetCashProvidedByUsedInInvestmentActivities", "Prior1InterimDuration", -9952000000),
        ]


class TestExtractInterim:
    def setup_method(self):
        self.out = _extract_interim(_FakeInterimReport())

    def test_period_metadata(self):
        assert self.out["report_period"] == "2026-03-31"
        assert self.out["period_start"] == "2025-10-01"
        assert self.out["period_type"] == "HY"
        assert self.out["fiscal_year_end"] == "2026-09-30"
        assert self.out["cumulative"] is True

    def test_current_period_consolidated(self):
        # 当中間期・連結の InterimDuration を拾う (前年/単体/セグメントではない)
        assert self.out["revenue"] == 478584000000
        assert self.out["operating_income"] == 52459000000

    def test_net_income_prefers_owners(self):
        # ProfitLoss(35,584) ではなく親会社株主帰属(27,336)を優先
        assert self.out["net_income"] == 27336000000

    def test_segment_breakdown_excluded(self):
        # _GameSegmentMember (132,227) を売上に混入させない
        assert self.out["revenue"] != 132227000000

    def test_balance_sheet_current_instant(self):
        # InterimInstant を拾う (Prior1YearInstant 557,162 ではない)
        assert self.out["total_assets"] == 556509000000
        assert self.out["net_assets"] == 284384000000

    def test_fcf_and_equity_ratio(self):
        assert self.out["free_cash_flow"] == 19612000000 + (-48104000000)
        assert self.out["equity_ratio_pct"] == round(284384000000 / 556509000000 * 100, 1)

    def test_prior_year_same_period(self):
        prior = self.out["prior"]
        assert prior["report_period"] == "2025-03-31"
        assert prior["revenue"] == 421214000000
        assert prior["net_income"] == 15863000000
        assert prior["free_cash_flow"] == 23786000000 + (-9952000000)


class TestBuildInterimReport:
    def test_yoy_table(self):
        payload = _extract_interim(_FakeInterimReport())
        rep = build_interim_report("4751", payload)
        assert "最新中間決算 — 4751" in rep
        assert "2025-10-01 〜 2026-03-31" in rep
        assert "中間累計値である点に注意" in rep
        # 売上 4,786億 vs 4,212億 → +13.6%
        assert "| 売上高 | 4,786 | 4,212 | +13.6% |" in rep
        # 営業利益 YoY (+79.8%) — mapped属性の前年混同バグが直っていることの確認
        assert "+79.8%" in rep
        assert "自己資本比率 51.1%" in rep

    def test_missing_prior_safe(self):
        payload = {"report_period": "2026-03-31", "period_start": "2025-10-01",
                   "period_type": "HY", "revenue": 100e8, "prior": {}}
        rep = build_interim_report("9999", payload)
        assert "| 売上高 | 100 | — | — |" in rep
