"""Unit tests for src/tools/tdnet_xbrl.py — TDnet 短信サマリー iXBRL parser (no network)."""
from src.tools.tdnet_xbrl import (
    _ixbrl_facts,
    _period_type,
    build_quarter_report,
    parse_summary_ixbrl,
)


def _nf(name, ctx, value, scale="6", sign=None):
    s = f' sign="{sign}"' if sign else ""
    return (f'<ix:nonFraction contextRef="{ctx}" scale="{scale}"{s} '
            f'format="ixt:numdotdecimal" name="tse-ed-t:{name}" unitRef="JPY">{value}</ix:nonFraction>')


# 4751 FY2026 H1 (2026-03-31) の実値に基づく最小 iXBRL フィクスチャ
_FIXTURE = "<html><body>" + "".join([
    # 当期累計 (連結・実績)
    _nf("NetSales", "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember", "478,584"),
    _nf("OperatingIncome", "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember", "52,459"),
    _nf("OrdinaryIncome", "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember", "53,920"),
    _nf("ProfitAttributableToOwnersOfParent", "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember", "27,336"),
    # 単体は無視されるべき (連結優先)
    _nf("NetSales", "CurrentAccumulatedQ2Duration_NonConsolidatedMember_ResultMember", "100,000"),
    # 前年同期
    _nf("NetSales", "PriorAccumulatedQ2Duration_ConsolidatedMember_ResultMember", "421,214"),
    _nf("OperatingIncome", "PriorAccumulatedQ2Duration_ConsolidatedMember_ResultMember", "29,169"),
    _nf("ProfitAttributableToOwnersOfParent", "PriorAccumulatedQ2Duration_ConsolidatedMember_ResultMember", "15,863"),
    # 当期末BS
    _nf("TotalAssets", "CurrentAccumulatedQ2Instant_ConsolidatedMember_ResultMember", "556,509"),
    _nf("NetAssets", "CurrentAccumulatedQ2Instant_ConsolidatedMember_ResultMember", "284,384"),
    # 前期末BS は無視されるべき
    _nf("TotalAssets", "PriorYearInstant_ConsolidatedMember_ResultMember", "557,162"),
    # 通期会社予想
    _nf("NetSales", "CurrentYearDuration_ConsolidatedMember_ForecastMember", "880,000"),
    _nf("OperatingIncome", "CurrentYearDuration_ConsolidatedMember_ForecastMember", "50,000"),
    _nf("ProfitAttributableToOwnersOfParent", "CurrentYearDuration_ConsolidatedMember_ForecastMember", "25,000"),
    # マイナス値 (sign) の確認用ダミー営業CF (通常サマリーには無いが念のため)
    '<ix:nonNumeric name="tse-ed-t:FiscalYearEnd" contextRef="x">2026-09-30</ix:nonNumeric>',
]) + "</body></html>"


class TestIxbrlFacts:
    def test_scale_applied(self):
        facts = _ixbrl_facts(_FIXTURE)
        assert facts[("NetSales", "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember")] == 478584 * 1e6

    def test_sign_applied(self):
        html = _nf("CashFlowsFromInvestingActivities", "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember", "48,104", sign="-")
        facts = _ixbrl_facts(html)
        assert facts[("CashFlowsFromInvestingActivities", "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember")] == -48104 * 1e6


class TestPeriodType:
    def test_q2(self):
        assert _period_type(_ixbrl_facts(_FIXTURE)) == "Q2"

    def test_fy(self):
        facts = _ixbrl_facts(_nf("NetSales", "CurrentYearDuration_ConsolidatedMember_ResultMember", "880,000"))
        assert _period_type(facts) == "FY"


class TestParseSummary:
    def setup_method(self):
        self.out = parse_summary_ixbrl(_FIXTURE, period_end="2026-03-31")

    def test_meta(self):
        assert self.out["source"] == "tdnet"
        assert self.out["report_period"] == "2026-03-31"
        assert self.out["period_type"] == "Q2"
        assert self.out["fiscal_year_end"] == "2026-09-30"

    def test_current_consolidated_not_nonconsolidated(self):
        # 連結 478,584 を採用 (単体 100,000 ではない)
        assert self.out["revenue"] == 478584 * 1e6
        assert self.out["operating_income"] == 52459 * 1e6

    def test_net_income_owners(self):
        assert self.out["net_income"] == 27336 * 1e6

    def test_balance_sheet_current_not_prior(self):
        assert self.out["total_assets"] == 556509 * 1e6  # 前期末 557,162 ではない
        assert self.out["net_assets"] == 284384 * 1e6

    def test_equity_ratio(self):
        assert self.out["equity_ratio_pct"] == round(284384 / 556509 * 100, 1)

    def test_prior(self):
        assert self.out["prior"]["revenue"] == 421214 * 1e6
        assert self.out["prior"]["net_income"] == 15863 * 1e6

    def test_forecast(self):
        assert self.out["forecast_fy"]["revenue"] == 880000 * 1e6
        assert self.out["forecast_fy"]["net_income"] == 25000 * 1e6

    def test_cf_absent_is_none(self):
        # サマリーに CF が無ければ None / FCF も None
        assert self.out["operating_cf"] is None
        assert self.out["free_cash_flow"] is None


class TestForecastLowerFallback:
    def test_range_forecast_uses_lower(self):
        html = ("<html>"
                + _nf("OperatingIncome", "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember", "52,459")
                + _nf("OperatingIncome", "CurrentYearDuration_ConsolidatedMember_LowerMember", "50,000")
                + _nf("OperatingIncome", "CurrentYearDuration_ConsolidatedMember_UpperMember", "60,000")
                + "</html>")
        out = parse_summary_ixbrl(html, period_end="2026-03-31")
        assert out["forecast_fy"]["operating_income"] == 50000 * 1e6  # 保守的に Lower


class TestBuildQuarterReport:
    def test_yoy_and_progress(self):
        out = parse_summary_ixbrl(_FIXTURE, period_end="2026-03-31")
        rep = build_quarter_report("4751", out)
        assert "最新四半期決算 — 4751 (第2四半期(中間))" in rep
        assert "期末: 2026-03-31" in rep
        # 売上 4,786億 vs 4,212億 → +13.6%、通期予想 8,800億 → 進捗 54%
        assert "| 売上高 | 4,786 | 4,212 | +13.6% | 8,800 | 54% |" in rep
        # 営業益が通期予想を超過 (105%)
        assert "105%" in rep
        assert "自己資本比率" in rep

    def test_missing_safe(self):
        rep = build_quarter_report("9999", {"report_period": "2026-03-31", "period_type": "Q1",
                                            "revenue": 100e8, "prior": {}, "forecast_fy": {}})
        assert "| 売上高 | 100 | — | — | — | — |" in rep
