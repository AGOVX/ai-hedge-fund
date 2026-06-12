"""Unit tests for src/tools/watchlist_check.py — pure logic, no network."""
from datetime import date, datetime

from src.tools.technicals import Technicals
from src.tools.watchlist_check import (
    _extract_price_threshold,
    build_report,
    check_review_due,
    check_technical_signals,
    filter_recent_disclosures,
)

TODAY = date(2026, 6, 10)


def _tech(**kw) -> Technicals:
    base = dict(
        ticker="8001", as_of="2026-06-09", close=2050.0,
        dma200=1980.0, above_dma200=True, dma200_distance_pct=3.54,
        high_52w=2286.0, low_52w=1901.0,
        high_52w_prior=2286.0, low_52w_prior=1901.0,
        pct_from_52w_high=-10.32, volume_ratio_20d=1.0,
        breakout_52w_high=False, breakout_volume_confirmed=False,
        breakdown_52w_low=False,
    )
    base.update(kw)
    return Technicals(**base)


class TestReviewDue:
    def test_overdue(self):
        out = check_review_due({"next_review_due": "2026-06-01"}, TODAY)
        assert out["status"] == "overdue"
        assert out["days_left"] == -9

    def test_due_soon(self):
        out = check_review_due({"next_review_due": "2026-06-13"}, TODAY)
        assert out["status"] == "due_soon"
        assert out["days_left"] == 3

    def test_ok(self):
        assert check_review_due({"next_review_due": "2026-07-30"}, TODAY)["status"] == "ok"

    def test_unset(self):
        assert check_review_due({}, TODAY)["status"] == "unset"

    def test_date_object_from_yaml(self):
        # yaml.safe_load returns datetime.date for unquoted dates
        out = check_review_due({"next_review_due": date(2026, 6, 13)}, TODAY)
        assert out["status"] == "due_soon"

    def test_datetime_object_from_yaml(self):
        # yaml は時刻付き表記だと datetime を返す。datetime は date のサブクラス
        # なので isinstance(raw, date) だけだと datetime - date で TypeError になる
        out = check_review_due({"next_review_due": datetime(2026, 6, 13, 10, 0)}, TODAY)
        assert out["status"] == "due_soon"
        assert out["days_left"] == 3

    def test_datetime_string_with_time(self):
        out = check_review_due({"next_review_due": "2026-06-13 10:00:00"}, TODAY)
        assert out["status"] == "due_soon"


class TestPriceThreshold:
    def test_extracts(self):
        assert _extract_price_threshold("株価 1,540 円以下 (ベース適正価値 × 70%)") == 1540.0
        assert _extract_price_threshold("株価 368円以下に下落") == 368.0

    def test_no_pattern(self):
        assert _extract_price_threshold("深堀チェック 11/15 以上の完了") is None


class TestTechnicalSignals:
    def test_no_signals_quiet(self):
        assert check_technical_signals({}, _tech()) == []

    def test_breakout_alert(self):
        alerts = check_technical_signals({}, _tech(close=2300.0, breakout_52w_high=True, breakout_volume_confirmed=True))
        assert any("ブレイクアウト" in a for a in alerts)

    def test_breakdown_alert(self):
        alerts = check_technical_signals({}, _tech(close=1890.0, breakdown_52w_low=True))
        assert any("52週安値割れ" in a for a in alerts)

    def test_buffett_price_condition(self):
        entry = {"reentry_conditions": {"buffett": {"required_price": ["株価 1,540 円以下 (×70%)"]}}}
        alerts = check_technical_signals(entry, _tech(close=1500.0))
        assert any("Buffett 価格条件に到達" in a for a in alerts)

    def test_buffett_price_condition_not_met(self):
        entry = {"reentry_conditions": {"buffett": {"required_price": ["株価 1,540 円以下"]}}}
        assert check_technical_signals(entry, _tech(close=2050.0)) == []

    def test_none_tech(self):
        alerts = check_technical_signals({}, None)
        assert any("取得不可" in a for a in alerts)


class TestDisclosureFilter:
    def test_window(self):
        ds = [
            {"title": "old", "pubdate": "2026-05-01 15:00:00"},
            {"title": "new", "pubdate": "2026-06-05 15:00:00"},
            {"title": "bad-date", "pubdate": ""},
        ]
        out = filter_recent_disclosures(ds, TODAY, lookback_days=14)
        assert [d["title"] for d in out] == ["new"]


class TestBuildReport:
    def test_urgent_summary_includes_due_and_breakout(self):
        entries = [
            {"ticker": "8001", "name": "伊藤忠商事", "status": "watch", "next_review_due": "2026-06-13"},
            {"ticker": "4751", "name": "サイバーエージェント", "status": "pass", "next_review_due": "2026-06-20"},
        ]
        techs = {
            "8001": _tech(close=2300.0, breakout_52w_high=True, breakout_volume_confirmed=True),
            "4751": _tech(ticker="4751", close=1300.0),
        }
        ds = {"8001": [{"title": "2026年3月期 決算短信", "pubdate": "2026-06-03 15:00:00"}], "4751": []}

        report = build_report(entries, TODAY, techs, ds)
        assert "要対応サマリー" in report
        assert "レビュー期日接近: 2026-06-13" in report
        assert "ブレイクアウト" in report
        assert "決算関連開示あり" in report

    def test_empty_watchlist(self):
        report = build_report([], TODAY, {}, {})
        assert "特になし" in report

    def test_earnings_line_shown(self):
        entries = [{"ticker": "8001", "name": "伊藤忠商事", "status": "watch",
                    "next_review_due": "2026-07-30"}]
        earnings = {"8001": {"date": "2026-08-04", "source": "手動", "days_to": 55}}
        report = build_report(entries, TODAY, {"8001": _tech()}, {"8001": []}, earnings)
        assert "次回決算発表: 2026-08-04 (手動, あと55日)" in report

    def test_earnings_soon_goes_urgent(self):
        entries = [{"ticker": "8001", "name": "伊藤忠商事", "status": "watch",
                    "next_review_due": "2026-07-30"}]
        earnings = {"8001": {"date": "2026-06-13", "source": "推定", "days_to": 3}}
        report = build_report(entries, TODAY, {"8001": _tech()}, {"8001": []}, earnings)
        assert "決算発表接近" in report
        # 要対応サマリー (本文より先) にも上がっている
        summary = report.split("## 8001")[0]
        assert "決算発表接近" in summary

    def test_earnings_none_omitted(self):
        entries = [{"ticker": "8001", "name": "伊藤忠商事", "status": "watch",
                    "next_review_due": "2026-07-30"}]
        earnings = {"8001": {"date": None, "source": None, "days_to": None}}
        report = build_report(entries, TODAY, {"8001": _tech()}, {"8001": []}, earnings)
        assert "次回決算発表" not in report

    def test_backward_compatible_without_earnings(self):
        # earnings_by_ticker を渡さない既存呼び出しが壊れないこと
        entries = [{"ticker": "8001", "name": "伊藤忠商事", "status": "watch",
                    "next_review_due": "2026-07-30"}]
        report = build_report(entries, TODAY, {"8001": _tech()}, {"8001": []})
        assert "次回決算発表" not in report
        assert "8001 伊藤忠商事" in report

    def test_dma200_none_shows_na(self):
        # 履歴200日未満 (above_dma200 is None) は「下」扱いせず n/a 表示
        entries = [{"ticker": "9999", "name": "新規上場", "status": "watch",
                    "next_review_due": "2026-07-30"}]
        techs = {"9999": _tech(ticker="9999", dma200=None, above_dma200=None,
                               dma200_distance_pct=None)}
        report = build_report(entries, TODAY, techs, {"9999": []})
        assert "200DMA n/a" in report
